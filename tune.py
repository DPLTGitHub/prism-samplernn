import tensorflow as tf
import kerastuner as kt
import json
import random
import numpy as np
import argparse
import librosa

from samplernn import SampleRNN
#from dataset import (get_dataset, get_dataset_filenames_split)
from dataset import (get_dataset, find_files)
from train import optimizer_factory


def get_arguments():
    parser = argparse.ArgumentParser(description='PRiSM SampleRNN Model Tuner')
    parser.add_argument('--data_dir',                   type=str,            required=True,
                                                        help='Path to the directory containing the training data.')
    parser.add_argument('--num_epochs',                 type=int, default=30,
                                                        help='Number of training epochs')
    parser.add_argument('--type',                       type=str,            default='bayesian', choices=['bayesian', 'random_search'],
                                                        help='Type of tuning algorithm to use, either Bayesian Optimization or Random Search.')
    parser.add_argument('--val_pcnt',                   type=float,          default=0.1,
                                                        help='Percentage of data to reserve for validation.')
    parser.add_argument('--test_pcnt',                  type=float,          default=0.1,
                                                        help='Percentage of data to reserve for testing.')
    return parser.parse_args()

args = get_arguments()

# Create and compile the model
def build_model(hp):
    hp.Choice('big_frame_size', [32, 64, 128], default=64)
    hp.Choice('frame_size', [4, 2])
    model = SampleRNN(
        batch_size=hp.Choice('batch_size', [16, 32, 64, 128], default=32),
        frame_sizes=[
            hp['big_frame_size'] // hp['frame_size'],
            hp['big_frame_size']
        ],
        seq_len=hp.Choice('seq_len', [512, 1024, 2048], default=1024),
        q_type='mu-law',
        q_levels=256,
        dim=hp.Choice('dim', [1024, 2048], default=1024),
        rnn_type='gru',
        num_rnn_layers=hp.Choice('num_rnn_layers', [1, 2, 4, 8], default=4),
        emb_size=256,
        skip_conn=False
    )
    optimizer = tf.optimizers.Adam(
        learning_rate=hp.Choice('learning_rate', [1e-2, 1e-3, 1e-4], default=1e-3),
        epsilon=1e-4)
    model.compile(
        optimizer=optimizer,
        loss='sparse_categorical_crossentropy',
        metrics=['sparse_categorical_accuracy'],
    )
    return model

def get_dataset_filenames_split(data_dir, val_pcnt, test_pcnt):
    files = find_files(data_dir)
    if not files:
        raise ValueError("No audio files found in '{}'.".format(data_dir))
    #random.shuffle(files)
    num_files = len(files)
    test_start = int( (1 - test_pcnt) * num_files )
    val_start = int( (1 - test_pcnt - val_pcnt) * num_files )
    return files[: val_start], files[val_start : test_start], files[test_start :]


# Tuner subclass.
class SampleRNNTuner(kt.Tuner):

    def run_trial(self, trial, data_dir, val_pcnt, test_pcnt, *args, **kwargs):
        hp = trial.hyperparameters
        model = self.hypermodel.build(trial.hyperparameters)

        num_epochs = kwargs.get('num_epochs')

        (train_split, val_split, test_split) = get_dataset_filenames_split(data_dir, val_pcnt, test_pcnt)

        batch_size = model.batch_size
        seq_len = model.seq_len
        overlap = model.big_frame_size
        q_type = 'mu-law'
        q_levels = 256
        val_batch_size = min(batch_size, len(val_split))
        test_batch_size = min(batch_size, len(test_split))

        # Train, Val and Test Datasets
        train_dataset = get_dataset(train_split, num_epochs, batch_size, seq_len, overlap,
                                    drop_remainder=True, q_type=q_type, q_levels=q_levels)
        val_dataset = get_dataset(val_split, 1, val_batch_size, seq_len, overlap,
                                  drop_remainder=True, q_type=q_type, q_levels=q_levels)
        test_dataset = get_dataset(test_split, 1, test_batch_size, seq_len, overlap,
                                   drop_remainder=True, q_type=q_type, q_levels=q_levels)

        # Get subseqs per batch...
        samples0, _ = librosa.load(train_split[0], sr=None, mono=True)
        steps_per_batch = int(np.floor(len(samples0) / float(seq_len)))

        # Get subseqs per epoch...
        steps_per_epoch = len(train_split) // batch_size * steps_per_batch

        # Train...
        model.fit(
            train_dataset,
            epochs=num_epochs,
            steps_per_epoch=steps_per_epoch,
            shuffle=False,
            validation_data=val_dataset 
        )

        # Evaluate...
        (val_loss, _) = model.evaluate(
            test_dataset,
            steps=10,
            verbose=0
        )

        # If we completely override run_trial we need to call this at the end.
        # See https://keras-team.github.io/keras-tuner/documentation/tuners/#run_trial-method_1 
        self.oracle.update_trial(trial.trial_id, {'loss': val_loss})
        self.oracle.save_model(trial.trial_id, model)


# Random Search.
def create_random_search_optimizer(objective='loss', max_trials=2, seed=None):
    return SampleRNNTuner(
        oracle=kt.oracles.RandomSearch(
            objective=objective,
            max_trials=max_trials,
            seed=seed),
        hypermodel=build_model)

# Bayesian Optimization.
def create_bayesian_optimizer(objective='loss', max_trials=2, num_initial_points=None,
                              alpha=0.0001, beta=2.6, seed=None):
    return SampleRNNTuner(
        oracle=kt.oracles.BayesianOptimization(
            objective=objective,
            max_trials=max_trials,
            num_initial_points=num_initial_points,
            alpha=alpha,
            beta=beta,
            seed=seed),
        hypermodel=build_model)

tuner_factory = {
    'bayesian' : create_bayesian_optimizer,
    'random_search' : create_random_search_optimizer
}

tuner = tuner_factory[args.type]()

tuner.search(
    args.data_dir,
    val_pcnt=args.val_pcnt,
    test_pcnt=args.test_pcnt,
    num_epochs=args.num_epochs
)

print('\n')
print('Printing search summary...')
tuner.results_summary()
print('\n')
print('Printing best model...')
models = tuner.get_best_models()
print(models)


'''
nohup python tune.py \
  --data_dir ../datasets/dawn_of_midi \
  --num_epochs 2 \
  > tuner.log 2>&1 </dev/null &
'''