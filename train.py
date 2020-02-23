from __future__ import print_function
import argparse
import os
import sys
import time

import tensorflow as tf
import numpy as np

from samplernn import SampleRNN
from samplernn import (load_audio, write_wav, find_files)
from samplernn import (mu_law_encode, mu_law_decode)
from samplernn import (optimizer_factory, one_hot_encode)

from dataset import get_dataset


LOGDIR_ROOT = './logdir'
OUTDIR = './generated'
NUM_STEPS = int(1e5)
BATCH_SIZE = 1
BIG_FRAME_SIZE = 64
FRAME_SIZE = 16
SEQ_LEN = 1024
Q_LEVELS = 256
Q_ZERO = Q_LEVELS // 2
DIM = 1024
N_RNN = 1
EMB_SIZE = 256
LEARNING_RATE = 1e-3
MOMENTUM = 0.9
L2_REGULARIZATION_STRENGTH = 0
SILENCE_THRESHOLD = None
OUTPUT_DUR = 3 # Duration of generated audio in seconds
CHECKPOINT_EVERY = 5
MAX_CHECKPOINTS = 5
GENERATE_EVERY = 10
SAMPLE_RATE = 44100 # Sample rate of generated audio
MAX_GENERATE_PER_BATCH = 10
NUM_GPUS = 1


def get_arguments():
    parser = argparse.ArgumentParser(description='PRiSM TensorFlow SampleRNN')
    parser.add_argument('--data_dir',                   type=str,   required=True,
                                                        help='Path to the directory containing the training data')
    parser.add_argument('--num_gpus',                   type=int,   default=NUM_GPUS, help='Number of GPUs')
    parser.add_argument('--batch_size',                 type=int,   default=BATCH_SIZE, help='Batch size')
    parser.add_argument('--logdir_root',                type=str,   default=LOGDIR_ROOT,
                                                        help='Root directory for training log files')
    parser.add_argument('--output_dir',                 type=str,   default=OUTDIR,
                                                        help='Path to the directory for generated audio')
    parser.add_argument('--output_file_dur',            type=str,   default=OUTPUT_DUR,
                                                        help='Duration of generated audio files')
    parser.add_argument('--checkpoint_every',           type=int,   default=CHECKPOINT_EVERY)
    parser.add_argument('--num_steps',                  type=int,   default=NUM_STEPS)
    parser.add_argument('--learning_rate',              type=float, default=LEARNING_RATE)
    parser.add_argument('--sample_size',                type=int,   default=SAMPLE_SIZE)
    parser.add_argument('--sample_rate',                type=int,   default=SAMPLE_RATE,
                                                        help='Sample rate of the generated audio')
    parser.add_argument('--l2_regularization_strength', type=float, default=L2_REGULARIZATION_STRENGTH)
    parser.add_argument('--silence_threshold',          type=float, default=SILENCE_THRESHOLD)
    parser.add_argument('--optimizer',                  type=str,   default='adam', choices=optimizer_factory.keys(),
                                                        help='Type of training optimizer to use')
    parser.add_argument('--momentum',                   type=float, default=MOMENTUM)
    parser.add_argument('--seq_len',                    type=int,   default=SEQ_LEN,
                                                        help='Number of samples in each truncated BPTT pass')
    parser.add_argument('--frame_sizes',                type=int,   default=[FRAME_SIZE, BIG_FRAME_SIZE], nargs='*',
                                                        help='Number of samples per frame in each tier')
    #parser.add_argument('--q_levels',                   type=int,   default=Q_LEVELS, help='Number of audio quantization bins')
    parser.add_argument('--dim',                        type=int,   default=DIM,
                                                        help='Number of cells in every RNN and MLP layer')
    parser.add_argument('--n_rnn',                      type=int,   default=N_RNN, choices=list(range(1, 6)),
                                                        help='Number of RNN layers in each tier')
    parser.add_argument('--emb_size',                   type=int,   default=EMB_SIZE,
                                                        help='Size of the embedding layer')
    parser.add_argument('--max_checkpoints',            type=int,   default=MAX_CHECKPOINTS,
                                                        help='Maximum number of training checkpoints to keep')
    parsed_args = parser.parse_args()
    assert parsed_args.frame_sizes[0] < parsed_args.frame_sizes[1], 'Frame sizes should be specified in ascending order'
    # The following parameter interdependencies are sourced from the original implementation:
    # https://github.com/soroushmehr/sampleRNN_ICLR2017/blob/master/models/three_tier/three_tier.py
    assert parsed_args.seq_len % parsed_args.frame_sizes[1] == 0,\
        'seq_len should be evenly divisible by tier 2 frame size'
    assert parsed_args.frame_sizes[1] % parsed_args.frame_sizes[0] == 0,\
        'Tier 2 frame size should be evenly divisible by tier 1 frame size'
    return parsed_args


def generate(model, step, dur, sample_rate, outdir):
    num_samps = dur * sample_rate
    samples = np.zeros((model.batch_size, num_samps, 1), dtype='int32')
    samples[:, :model.big_frame_size, :] = Q_ZERO
    q_vals = np.arange(Q_LEVELS)
    for t in range(model.big_frame_size, num_samps):
        if t % model.big_frame_size == 0:
            inputs = samples[:, t - model.big_frame_size : t, :].astype('float32')
            big_frame_outputs = model.big_frame_rnn(
                inputs,
                num_steps=1,
                conditioning_frames=None)
        if t % model.frame_size == 0:
            inputs = samples[:, t - model.frame_size : t, :].astype('float32')
            big_frame_output_idx = (t // model.frame_size) % (
                model.big_frame_size // model.frame_size
            )
            frame_outputs = model.frame_rnn(
                inputs,
                num_steps=1,
                conditioning_frames=big_frame_outputs[:, big_frame_output_idx, :])
        inputs = samples[:, t - model.frame_size : t, :]
        frame_output_idx = t % model.frame_size
        sample_outputs = model.sample_mlp(
            inputs,
            conditioning_frames=frame_outputs[:, frame_output_idx, :])
        sample_outputs = tf.cast(
            tf.reshape(sample_outputs, [-1, Q_LEVELS]),
            tf.float64
        )
        sample_next_list = []
        for row in tf.cast(tf.nn.softmax(sample_outputs), tf.float32):
            samp = np.random.choice(q_vals, p=row)
            sample_next_list.append(samp)
        samples[:, t] = np.array(sample_next_list).reshape([-1, 1])
    template = '{}/step_{}.{}.wav'
    for i in range(model.batch_size):
        samples = samples[i].reshape([-1, 1]).tolist()
        audio = mu_law_decode(samples, Q_LEVELS)
        path = template.format(outdir, str(step), str(i))
        write_wav(path, audio, sample_rate)
        if i >= MAX_GENERATE_PER_BATCH: break


def main():
    args = get_arguments()
    if not find_files(args.data_dir):
        raise ValueError("No audio files found in '{}'.".format(args.data_dir))
    if args.l2_regularization_strength == 0:
        args.l2_regularization_strength = None
    logdir = os.path.join(args.logdir_root, 'train')
    if not os.path.exists(logdir):
        os.makedirs(logdir)

    model = SampleRNN(
        batch_size=args.batch_size,
        frame_sizes=args.frame_sizes,
        q_levels=Q_LEVELS, #args.q_levels,
        dim=args.dim,
        n_rnn=args.n_rnn,
        seq_len=args.seq_len,
        emb_size=args.emb_size,
    )
    opt = optimizer_factory[args.optimizer](
        learning_rate=args.learning_rate,
        momentum=args.momentum,
    )

    overlap = model.big_frame_size
    dataset = get_dataset(args.data_dir, args.batch_size, args.seq_len, overlap)

    def train_iter():
        for batch in dataset:
            reset = True
            num_samps = len(batch[0])
            for i in range(0, num_samps, seq_len):
                seqs = batch[:, i : i+seq_len+overlap]
                yield (seqs, reset)
                reset = False

    checkpoint_prefix = os.path.join(logdir, 'ckpt')
    checkpoint = tf.train.Checkpoint(optimizer=opt, model=model)
    writer = tf.summary.create_file_writer(logdir)
    tf.summary.trace_on(graph=True, profiler=True)

    @tf.function
    def train_step(inputs):
        with tf.GradientTape() as tape:
            inputs = mu_law_encode(inputs, Q_LEVELS)
            encoded_rnn = one_hot_encode(inputs, batch_size, Q_LEVELS)
            raw_output = model(
                inputs,
                training=True,
            )
            target = tf.reshape(encoded_rnn[:, model.big_frame_size:, :], [-1, Q_LEVELS])
            prediction = tf.reshape(raw_output, [-1, Q_LEVELS])
            loss = tf.reduce_mean(
                tf.nn.sparse_softmax_cross_entropy_with_logits(
                    logits=prediction,
                    labels=tf.argmax(target, axis=-1)))
        grads = tape.gradient(loss, model.trainable_variables)
        grads = tf.clip_by_global_norm(grads, 5.0)
        opt.apply_gradients(list(zip(grads, model.trainable_variables)))
        return loss

    for (step, (inputs, reset)) in enumerate(train_iter()):
        if (step-1) % GENERATE_EVERY == 0 and step > GENERATE_EVERY:
            print('Generating samples...')
            #generate_and_save_samples(model, step, args.output_file_dur, args.sample_rate, args.output_dir)

        #if reset: model.reset_hidden_states()

        start_time = time.time()
        loss = train_step(inputs)
        if reset: model.reset_hidden_states()

        with writer.as_default():
            tf.summary.scalar('loss', loss, step=step)
            #writer.flush() # But see https://stackoverflow.com/a/52502679

        duration = time.time() - start_time
        template = 'Step {:d}: Loss = {:.3f}, ({:.3f} sec/step)'
        print(template.format(step, loss, duration))

        if step % 20 == 0:
            checkpoint.save(checkpoint_prefix)
            print('Storing checkpoint to {} ...'.format(logdir), end="")
            sys.stdout.flush()
        
        if step == 0:
            with writer.as_default():
                tf.summary.trace_export(
                    name="samplernn_model_trace",
                    step=0,
                    profiler_outdir=logdir)


if __name__ == '__main__':
    main()
