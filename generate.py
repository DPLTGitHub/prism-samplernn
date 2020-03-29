from __future__ import print_function
import argparse
import os
import sys
import time
import json

import tensorflow as tf
import numpy as np
import librosa

from samplernn import (SampleRNN, write_wav, quantize, dequantize, unsqueeze)


OUTPUT_DUR = 3 # Duration of generated audio in seconds
SAMPLE_RATE = 44100 # Sample rate of generated audio
NUM_SEQS = 1
SAMPLING_TEMPERATURE = 0.95
SEED_OFFSET = 0


def get_arguments():
    def check_positive(value):
        val = int(value)
        if val < 1:
             raise argparse.ArgumentTypeError("%s is not positive" % value)
        return val

    parser = argparse.ArgumentParser(description='PRiSM TensorFlow SampleRNN Generator')
    parser.add_argument('--output_path',                type=str,            required=True,
                                                        help='Path to the generated .wav file')
    parser.add_argument('--checkpoint_path',            type=str,            required=True,
                                                        help='Path to a saved checkpoint for the model')
    parser.add_argument('--config_file',                type=str,            required=True,
                                                        help='Path to the JSON config for the model')
    parser.add_argument('--dur',                        type=check_positive, default=OUTPUT_DUR,
                                                        help='Duration of generated audio')
    parser.add_argument('--num_seqs',                   type=check_positive, default=NUM_SEQS,
                                                        help='Number of audio sequences to generate')
    parser.add_argument('--sample_rate',                type=check_positive, default=SAMPLE_RATE,
                                                        help='Sample rate of the generated audio')
    parser.add_argument('--temperature',                type=float,          default=SAMPLING_TEMPERATURE,
                                                        help='Sampling temperature')
    parser.add_argument('--seed',                       type=str,            help='Path to audio for seeding')
    parser.add_argument('--seed_offset',                type=int,            default=SEED_OFFSET,
                                                        help='Starting offset of the seed audio')
    return parser.parse_args()


# On generation speed: https://github.com/soroushmehr/sampleRNN_ICLR2017/issues/19
# Speed again: https://ambisynth.blogspot.com/2018/09/wavernn.html
# On seeding (sort of): https://github.com/soroushmehr/sampleRNN_ICLR2017/issues/11
# Very interesting article on sampling temperature (including the idea of varying it
# while sampling): https://www.robinsloan.com/expressive-temperature/

def generate_and_save_samples(model, path, seed, seed_offset=0, dur=OUTPUT_DUR,
                              sample_rate=SAMPLE_RATE, temperature=SAMPLING_TEMPERATURE):
    q_type = model.q_type
    q_levels = model.q_levels
    q_zero = q_levels // 2
    num_samps = dur * sample_rate
    samples = np.zeros((model.batch_size, model.big_frame_size + num_samps, 1), dtype='int32')
    if seed is not None:
        seed_audio = load_seed_audio(seed, seed_offset, model.big_frame_size)
        samples[:, :model.big_frame_size, :] = quantize(seed_audio, q_type, q_levels)
    else:
        samples[:, :model.big_frame_size, :] = q_zero
    progress_every = 250
    start_time = time.time()
    for t in range(model.big_frame_size, model.big_frame_size + num_samps):
        if t % model.big_frame_size == 0:
            inputs = samples[:, t - model.big_frame_size : t, :].astype('float32')
            big_frame_outputs = model.big_frame_rnn(
                inputs,
                num_steps=1)
        if t % model.frame_size == 0:
            inputs = samples[:, t - model.frame_size : t, :].astype('float32')
            big_frame_output_idx = (t // model.frame_size) % (
                model.big_frame_size // model.frame_size
            )
            frame_outputs = model.frame_rnn(
                inputs,
                num_steps=1,
                conditioning_frames=unsqueeze(big_frame_outputs[:, big_frame_output_idx, :], 1))
        inputs = samples[:, t - model.frame_size : t, :]
        frame_output_idx = t % model.frame_size
        sample_outputs = model.sample_mlp(
            inputs,
            conditioning_frames=unsqueeze(frame_outputs[:, frame_output_idx, :], 1))
        sample_outputs = sample_outputs / temperature
        sample_outputs = tf.reshape(sample_outputs, [-1, q_levels])
        generated = tf.random.categorical(sample_outputs, 1)
        start = t - model.big_frame_size
        if start % progress_every == 0:
            end = min(start + progress_every, num_samps)
            duration = time.time() - start_time
            template = 'Generating samples {} - {} of {} (time elapsed: {:.3f} seconds)'
            print(template.format(start+1, end, num_samps, duration))
        samples[:, t] = np.array(generated).reshape([-1, 1])
    path = path.split('.wav')[0]
    for i in range(model.batch_size):
        batch_samples = samples[i].reshape([-1, 1])[model.big_frame_size :].tolist()
        audio = dequantize(batch_samples, q_type, q_levels)
        file_name = '{}_{}'.format(path, str(i)) if model.batch_size > 1 else path
        file_name = '{}.wav'.format(file_name)
        write_wav(file_name, audio, sample_rate)
        print('Generated sample output to {}'.format(file_name))
    print('Done')


def load_seed_audio(path, offset, length):
    (audio, _) = librosa.load(path, sr=None, mono=True)
    assert offset + length <= len(audio), 'Seed offset plus length exceeds audio length'
    chunk = audio[offset : offset + length]
    return chunk.reshape(-1, 1)


def create_inference_model(ckpt_path, num_seqs, config):
    model = SampleRNN(
        batch_size=num_seqs,
        frame_sizes=config['frame_sizes'],
        q_type=config['q_type'],
        q_levels=config['q_levels'],
        dim=config['dim'],
        num_rnn_layers=config['num_rnn_layers'],
        seq_len=config['seq_len'],
        emb_size=config['emb_size'],
    )
    num_samps = config['seq_len'] + model.big_frame_size
    init_data = np.zeros((model.batch_size, num_samps, 1), dtype='int32')
    model(init_data)
    model.load_weights(ckpt_path)
    return model


def generate(path, ckpt_path, config, num_seqs=NUM_SEQS, dur=OUTPUT_DUR, sample_rate=SAMPLE_RATE,
             temperature=SAMPLING_TEMPERATURE, seed=None, seed_offset=None):
    model = create_inference_model(ckpt_path, num_seqs, config)
    generate_and_save_samples(model, path, seed, seed_offset, dur, sample_rate, temperature)


def main():
    args = get_arguments()
    with open(args.config_file, 'r') as config_file:
        config = json.load(config_file)
    generate(args.output_path, args.checkpoint_path, config, args.num_seqs, args.dur,
             args.sample_rate, args.temperature, args.seed, args.seed_offset)


if __name__ == '__main__':
    main()