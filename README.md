# PRiSM SampleRNN  

PRiSM implementation of [SampleRNN: An Unconditional End-to-End Neural Audio Generation Model](https://arxiv.org/abs/1612.07837), using TensorFlow 2.

## Features

- Three-tier architecture
- GRU cell RNN
- Choice of mu-law or linear quantization

## Requirements
- TensorFlow 2
- Librosa

## Installation
The simplest way to install is with [Anaconda](https://www.anaconda.com/distribution/). When you have that all set up create a new environment with:

`conda create -n prism-samplernn anaconda`

We're naming the environment after the repo, but you can choose whatever name you like. Then activate it with:

`conda activate prism-samplernn`

Finally run requirements.txt to install the dependencies:

`pip install -r requirements.txt`

## Architecture

The architecture of the network conforms to the three-tier design proposed in the original paper, consisting of two upper [RNN](https://www.tensorflow.org/guide/keras/rnn) tiers and a lower [MLP](https://en.wikipedia.org/wiki/Multilayer_perceptron) tier. The two upper tiers operate on frames of samples, while the lower tier is at the level of individual samples.

## Training

### Preparing data

SampleRNN is designed to accept raw audio in the form of .wav files. We therefore need to preprocess our source .wav file by slicing it into chunks, using the supplied [chunk_audio.py](https://bitbucket.org/cmelen/prism-samplernn.py/master/chunk_audio.py) script:
```
python chunk_audio.py <path_to_input_wav> ./chunks/ --chunk_length 8000 --overlap 1000
```
The second argument (required) is the path to the directory to contain the chunks - note the trailing slash (required, otherwise the chunks will be created in the current directory). You will need to create this directory (the above places the chunks in a sub-directory called 'chunks' within the current directory). The script has two optional arguments for setting the chunk_length (defaults to 8000 ms), and an overlap betweem consecutive chunks (defaults to 0 ms, no overlap).

### Running the training script

Assuming your training corpus is stored in a directory named `data` under the present directory, you can run the train.py script as follows:

```shell
python train.py \
  --id test \
  --data_dir ./data \
  --num_epochs 100 \
  --batch_size 128 \
  --max_checkpoints 2 \
  --checkpoint_every 200 \
  --output_file_dur 3 \
  --sample_rate 16000
```

The current epoch, training step and training loss are printed to the terminal during training. Temporary checkpoints storing the current state of the model are periodically saved to disk during each epoch, with a permanent checkpoint saved at the end of each epoch. An audio file is also generated at the end of an epoch, which may be used to assess the progress of the training.

### Command Line Arguments

The following table lists the hyper-parameters that may be passed at the command line:

| Parameter Name             | Description           | Default Value  | Required?   |
| ---------------------------|-----------------------|----------------| -----------|
| `id`                     | Id for the training session          | None           | Yes        |
| `data_dir`               | Path to the directory containing the training data           | None           | Yes        |
| `logdir_root`            | Location in which to store training log files and checkpoints. All such files are placed in a subdirectory with the id of the training session.           | ./logdir           | No      |
| `output_dir`             | Path to the directory for audio generated during training           | ./generated           | No      |
| `config_file`            | File containing the configuration parameters for the training model. Note that this file must contain valid JSON, and have the `.json` extension. | ./default.json         | No        |
| `num_epochs`             | Number of epochs to run the training | 100           | No        |
| `batch_size`             | Size of the mini-batch | 64         | No        |
| `optimizer`              | TensorFlow optimizer to use for training (`adam`, `sgd` or `rmsprop`) | `adam`        | No        |
| `learning_rate`          | Learning rate of the training optimizer   | 0.001         | No        |
| `momentum`               | Momentum of the training optimizer   | 0.9      | No        |
| `checkpoint_every`       | Interval (in steps) at which to generate a checkpoint file   | 100      | No        |
| `max_checkpoints`        | Maximum number of training checkpoints to keep   | 5      | No        |
| `resume`                 | Whether to resume training from the last available checkpoint   | True      | No        |
| `max_generate_per_epoch` | Maximum number of output files to generate at the end of each epoch   | 1      | No        |
| `sample_rate`            | Sample rate of the generated audio | 44100         | No        |
| `output_file_dur`        | Duration of generated audio files (in seconds) | 3         | No        |
| `temperature`            | Sampling temperature for generated audio | 0.95         | No        |
| `seed`                   | Path to audio for seeding when generating audio | None         | No        |
| `seed_offset`            | Starting offset of the seed audio | 0         | No        |
| `val_pcnt`               | Percentage of data to reserve for validation | 0.1         | No        |
| `test_pcnt`              | Percentage of data to reserve for testing | 0.1         | No        |

### Configuring the Model

Model parameters are specified through a JSON configuration file, which may be passed to the training script through the `--config_file` parameter (defaults to ./default.json). The following table lists the available model parameters (note that all parameters are optional and have defaults):

| Parameter Name           | Description           | Default Value  |
| -------------------------|-----------------------|----------------|
| `seq_len`                | RNN sequence length. Note that the value must be evenly-divisible by the top tier frame size.        | 1024           |
| `frame_sizes`            | Frame sizes (in samples) of the two upper tiers in the architecture, in ascending order. Note that the frame size of the upper tier must be an even multiple of that of the lower tier.  | [16,64]            |
| `dim`                    | RNN hidden layer dimensionality          | 1024         | 
| `num_rnn_layers`         | Depth of the RNN in each of the two upper tiers           | 4          |
| `q_type`                 | Quantization type (`mu-law` or `linear`)          | `mu-law`          |
| `q_levels`               | Number of quantization channels (note that if `q_type` is `mu-law` this parameter is ignored, as Mu-Law quantization requires 256 channels)     | 256           |
| `emb_size`               | Size of the embedding layer in the bottom tier (sample-level MLP)         | 256          |

## Generating Audio

To generate audio from a trained model use the generate.py script:

```shell
python generate.py \
  --output_path ./generated/test.wav \
  --checkpoint_path ./logdir/test/predict/ckpt-0 \
  --num_seqs 2 \
  --dur 1 \
  --sample_rate 16000 \
  --seed './path/to/seed.wav' \
  --seed_offset 500 \
  --config_file ./default.json
```

The model to generate from is specified by the path to an available epoch checkpoint. The generation stage must use the same parameters as the trained model, contained in a JSON config file.

Use the `--num_seqs` parameter to specify the number of audio sequences to generate. Sequences are generated in parallel in a batch, so for large values for `--num_seqs` the operation will be faster than real time.

To seed the generation pass an audio file as the `--seed` parameter. An offset into the file (in samples) may be specified using `--seed_offset`. Note that the size of the seed audio is truncated to the large frame size set during training (64 samples by default).