import os
import yaml
import datetime

import tensorflow as tf
from tensorflow.keras.metrics import Precision, Recall, AUC
from tensorflow_addons.metrics import F1Score
from tensorflow.keras.models import save_model
from tensorflow.keras.callbacks import EarlyStopping, TensorBoard, ReduceLROnPlateau
from tensorflow.keras import backend as k
from tensorflow.keras.callbacks import Callback
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split, KFold
from skopt import gp_minimize
from skopt.space import Real, Categorical, Integer

from src.models.models import *
from src.visualization.visualization import *
from src.data.preprocessor import Preprocessor
import gc

cfg = yaml.full_load(open(os.getcwd() + "/config.yml", 'r'))
CUR_DATETIME = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

# for device in tf.config.experimental.list_physical_devices("GPU"):
#     tf.config.experimental.set_memory_growth(device, True)

def get_class_weights(histogram):
    '''
    Computes weights for each class to be applied in the loss function during training.
    :param histogram: A list depicting the number of each item in different class
    :param class_multiplier: List of values to multiply the calculated class weights by. For further control of class weighting.
    :return: A dictionary containing weights for each class
    '''
    weights = [None] * len(histogram)
    for i in range(len(histogram)):
        weights[i] = (1.0 / len(histogram)) * sum(histogram) / histogram[i]
    class_weight = {i: weights[i] for i in range(len(histogram))}
    print("Class weights: ", class_weight)
    return class_weight


def define_callbacks(cfg):
    '''
    Defines a list of Keras callbacks to be applied to model training loop
    :param cfg: Project config object
    :return: list of Keras callbacks
    '''
    early_stopping = EarlyStopping(monitor='val_loss', verbose=1, patience=cfg['TRAIN']['PATIENCE'], mode='min',
                                   restore_best_weights=True)

    reduce_lr = ReduceLROnPlateau(monitor='val_loss', factor=0.5, patience=cfg['TRAIN']['PATIENCE'] // 2 + 1, verbose=1,
                                  min_lr=1e-8, min_delta=0.0001)

    # class ClearMemory(Callback):
    #     def on_epoch_end(self, epoch, logs=None):
    #         gc.collect()
    #         k.clear_session()

    callbacks = [early_stopping, reduce_lr]

    return callbacks


def partition_dataset(val_split, test_split, save_dfs=True):
    '''
    Partition the frame_df into training, validation and test sets by patient ID
    :param val_split: Validation split (in range [0, 1])
    :param test_split: Test split (in range [0, 1])
    :param save_dfs: Flag indicating whether to save the splits
    :return: (Training DataFrame, validation DataFrame, test DataFrame)
    '''

    frame_df = pd.read_csv(cfg['PATHS']['FRAMES_TABLE'])
    all_pts = frame_df['Patient'].unique()  # Get list of patients
    relative_val_split = val_split / (1 - (test_split))
    random_state = cfg['TRAIN']['RANDOM_STATE']
    print('Splitting data with random state {}.'.format(random_state))
    trainval_pts, test_pts = train_test_split(all_pts, test_size=test_split, random_state=random_state)
    train_pts, val_pts = train_test_split(trainval_pts, test_size=relative_val_split, random_state=random_state)

    train_df_frames = frame_df[frame_df['Patient'].isin(train_pts)]
    val_df_frames = frame_df[frame_df['Patient'].isin(val_pts)]
    test_df_frames = frame_df[frame_df['Patient'].isin(test_pts)]

    train_df_frames['Clip'] = train_df_frames['Frame Path'].str.rsplit('_', 1, expand=True)[0]
    val_df_frames['Clip'] = val_df_frames['Frame Path'].str.rsplit('_', 1, expand=True)[0]
    test_df_frames['Clip'] = test_df_frames['Frame Path'].str.rsplit('_', 1, expand=True)[0]

    train_df_clips = train_df_frames.groupby('Clip').first().reset_index().drop('Frame Path', axis=1).rename(columns={'Clip': 'filename'})
    val_df_clips = val_df_frames.groupby('Clip').first().reset_index().drop('Frame Path', axis=1).rename(columns={'Clip': 'filename'})
    test_df_clips = test_df_frames.groupby('Clip').first().reset_index().drop('Frame Path', axis=1).rename(columns={'Clip': 'filename'})

    print('TRAIN/VAL/TEST SPLIT: [{}, {}, {}] frames, [{}, {}, {}] clips, [{}, {}, {}] patients'
          .format(train_df_frames.shape[0], val_df_frames.shape[0], test_df_frames.shape[0],
                  train_df_clips.shape[0], val_df_clips.shape[0], test_df_clips.shape[0],
                  train_pts.shape[0], val_pts.shape[0], test_pts.shape[0]))

    if save_dfs:
        partition_dir = os.path.join(cfg['PATHS']['PARTITIONS_DIR'], CUR_DATETIME)
        os.makedirs(partition_dir)
        train_df_frames.to_csv(os.path.join(partition_dir, 'train_set_frames.csv'), index=False)
        val_df_frames.to_csv(os.path.join(partition_dir, 'val_set_frames.csv'), index=False)
        test_df_frames.to_csv(os.path.join(partition_dir, 'test_set_frames.csv'), index=False)
        train_df_clips.to_csv(os.path.join(partition_dir, 'train_set_clips.csv'), index=False)
        val_df_clips.to_csv(os.path.join(partition_dir, 'val_set_clips.csv'), index=False)
        test_df_clips.to_csv(os.path.join(partition_dir, 'test_set_clips.csv'), index=False)
    return train_df_frames, val_df_frames, test_df_frames


def log_test_results(model, test_set, test_df, test_metrics, log_dir):
    '''
    Visualize performance of a trained model on the test set. Optionally save the model.
    :param model: A trained TensorFlow model
    :param test_set: A TensorFlow image generator for the test set
    :param test_metrics: Dict of test set performance metrics
    :param log_dir: Path to write TensorBoard logs
    '''

    # Visualization of test results
    test_predictions = model.predict(test_set, verbose=0)
    test_labels = test_df['Class'].to_numpy()
    plt = plot_roc(test_labels, test_predictions, list(range(len(cfg['DATA']['CLASSES']))))
    roc_img = plot_to_tensor()
    plt = plot_confusion_matrix(test_labels, test_predictions, list(range(len(cfg['DATA']['CLASSES']))))
    cm_img = plot_to_tensor()

    # Log test set results and plots in TensorBoard
    writer = tf.summary.create_file_writer(logdir=log_dir)

    # Create table of test set metrics
    test_summary_str = [['**Metric**','**Value**']]
    for metric in test_metrics:
        metric_values = test_metrics[metric]
        test_summary_str.append([metric, str(metric_values)])

    # Create table of model and train hyperparameters used in this experiment
    hparam_summary_str = [['**Hyperparameter**', '**Value**']]
    for key in cfg['TRAIN']:
        hparam_summary_str.append([key, str(cfg['TRAIN'][key])])
    for key in cfg['HPARAMS'][cfg['TRAIN']['MODEL_DEF'].upper()]:
        hparam_summary_str.append([key, str(cfg['HPARAMS'][cfg['TRAIN']['MODEL_DEF'].upper()][key])])

    # Write to TensorBoard logs
    with writer.as_default():
        tf.summary.text(name='Test set metrics', data=tf.convert_to_tensor(test_summary_str), step=0)
        tf.summary.text(name='Run hyperparameters', data=tf.convert_to_tensor(hparam_summary_str), step=0)
        tf.summary.image(name='ROC Curve (Test Set)', data=roc_img, step=0)
        tf.summary.image(name='Confusion Matrix (Test Set)', data=cm_img, step=0)
    return

def train_model(model_def, preprocessing_fn, train_df, val_df, test_df, hparams, save_weights=False, log_dir=None, verbose=True):
    '''
    :param model_def: Model definition function
    :param preprocessing_fn: Model-specific preprocessing function
    :param train_df: Training set of LUS frames
    :param val_df: Validation set of LUS frames
    :param test_df: Test set of LUS frames
    :param hparams: Dict of hyperparameters
    :param save_weights: Flag indicating whether to save the model's weights
    :param log_dir: TensorBoard logs directory
    :param verbose: Whether to print out all epoch details
    :return: (model, test_metrics, test_generator)
    '''

    # Create TF datasets for training, validation and test sets
    frames_dir = cfg['PATHS']['FRAMES_DIR']
    train_set = tf.data.Dataset.from_tensor_slices(([os.path.join(frames_dir, f) for f in train_df['Frame Path'].tolist()], train_df['Class']))
    val_set = tf.data.Dataset.from_tensor_slices(([os.path.join(frames_dir, f) for f in val_df['Frame Path'].tolist()], val_df['Class']))
    test_set = tf.data.Dataset.from_tensor_slices(([os.path.join(frames_dir, f) for f in test_df['Frame Path'].tolist()], test_df['Class']))
    # Set up preprocessing transformations to apply to each item in dataset
    preprocessor = Preprocessor(preprocessing_fn)
    train_set = preprocessor.prepare(train_set, shuffle=True, augment=True)
    val_set = preprocessor.prepare(val_set, shuffle=False, augment=False)
    test_set = preprocessor.prepare(test_set, shuffle=False, augment=False)

    # Get class weights based on prevalences
    histogram = np.bincount(train_df['Class'].to_numpy().astype(int))  # Get class distribution
    class_weight = get_class_weights(histogram)

    # Define performance metrics
    classes = cfg['DATA']['CLASSES']
    # F1Score(name='f1score', num_classes=2)
    metrics = ['accuracy', AUC(name='auc'), Precision(name='precision', thresholds=0.5),
               Recall(name='recall', thresholds=0.5)]

    print('Training distribution: ',
          ['Class ' + classes[i] + ': ' + str(histogram[i]) + '. '
           for i in range(len(histogram))])
    input_shape = cfg['DATA']['IMG_DIM'] + [3]

    # Compute output bias
    output_bias = np.log(histogram[1] / histogram[0])

    # Define the model
    model = model_def(hparams, input_shape, metrics, cfg['TRAIN']['N_CLASSES'], output_bias=output_bias)

    # Set training callbacks.
    callbacks = define_callbacks(cfg)
    if log_dir is not None:
        tensorboard = TensorBoard(log_dir=log_dir, histogram_freq=1)
        callbacks.append(tensorboard)

    # Train the model.
    history = model.fit(train_set, epochs=cfg['TRAIN']['EPOCHS'], validation_data=val_set, callbacks=callbacks,
                         verbose=verbose, class_weight=class_weight)

    # Save the model's weights
    if save_weights:
        model_path = cfg['PATHS']['MODEL_WEIGHTS'] + 'model' + CUR_DATETIME + '-' + cfg['TRAIN']['MODEL_DEF'] + '.h5'
        if cfg['TRAIN']['MODEL_DEF'] == 'cutoffvgg16':
            save_model(model.model, model_path)
        else:
            save_model(model, model_path)  # Save the model's weights

    # Run the model on the test set and print the resulting performance metrics.
    test_results = model.evaluate(test_set, verbose=1)
    test_metrics = {}
    test_summary_str = [['**Metric**', '**Value**']]
    for metric, value in zip(model.metrics_names, test_results):
        test_metrics[metric] = value
        test_summary_str.append([metric, str(value)])
    if log_dir is not None:
        log_test_results(model, test_set, test_df, test_metrics, log_dir)
    return model, test_metrics, test_set


def train_single(hparams=None, save_weights=False, write_logs=False, save_partitions=True):
    '''
    Train a single model. Use the passed hyperparameters if possible; otherwise, use those in config.
    :param hparams: Dict of hyperparameters
    :param save_model: Flag indicating whether to save the model
    :param write_logs: Flag indicating whether to write any training logs to disk
    :param save_partitions: Flag indicating whether to save train/val/test partitions
    :return: Dictionary of test set performance metrics
    '''
    train_df, val_df, test_df = partition_dataset(cfg['DATA']['VAL_SPLIT'], cfg['DATA']['TEST_SPLIT'],
                                                  save_dfs=save_partitions)
    model_def, preprocessing_fn = get_model(cfg['TRAIN']['MODEL_DEF'])
    if write_logs:
        log_dir = os.path.join(cfg['PATHS']['LOGS'], CUR_DATETIME + '-' + cfg['TRAIN']['MODEL_DEF'])
    else:
        log_dir = None

    # Specify hyperparameters if not already done
    if hparams is None:
        hparams = cfg['HPARAMS'][cfg['TRAIN']['MODEL_DEF'].upper()]

    # Train the model
    model, test_metrics, _ = train_model(model_def, preprocessing_fn, train_df, val_df, test_df, hparams,
                                         save_weights=save_weights, log_dir=log_dir)
    print('Test set metrics: ', test_metrics)
    return test_metrics, model


def save_hparam_search_results(init_dict, score, objective_metric, hparam_names, hparams, model_name, cur_datetime):
    '''
    Saves the results of a hyperparameter search in a table and a partial dependence plot
    :param init_dict: Initial results dictionary (hyperparameter names for keys and empty lists as values)
    :param score: Objective score for current iteration
    :param objective_metric: Name of the objective metric
    :param hparams: Hyperparameter value dictionary
    :param model_name: Name of model
    :param cur_datetime: String representation of current date and time
    :return: Results dictionary
    '''

    # Create table to detail results
    results_path = cfg['PATHS']['EXPERIMENTS'] + 'hparam_search_' + model_name + \
                   cur_datetime + '.csv'
    if os.path.exists(results_path):
        results = pd.read_csv(results_path).to_dict(orient='list')
    else:
        results = init_dict
    trial_idx = len(results['Trial'])
    results['Trial'].append(str(trial_idx))
    results[objective_metric].append(1.0 - score)
    for hparam_name in hparam_names:
        results[hparam_name].append(hparams[hparam_name])

    results_df = pd.DataFrame(results)
    results_df.to_csv(results_path, index_label=False, index=False)
    return results

def bayesian_hparam_optimization():
    '''
    Conducts a Bayesian hyperparameter optimization, given the parameter ranges and selected model
    :return: Dict of hyperparameters deemed optimal
    '''
    model_name = cfg['TRAIN']['MODEL_DEF'].upper()
    cur_datetime = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    objective_metric = cfg['HPARAM_SEARCH']['OBJECTIVE']
    results = {'Trial': [], objective_metric: []}
    dimensions = []
    default_params = []
    hparam_names = []
    for hparam_name in cfg['HPARAM_SEARCH'][model_name]:
        if cfg['HPARAM_SEARCH'][model_name][hparam_name]['RANGE'] is not None:
            if cfg['HPARAM_SEARCH'][model_name][hparam_name]['TYPE'] == 'set':
                dimensions.append(Categorical(categories=cfg['HPARAM_SEARCH'][model_name][hparam_name]['RANGE'],
                                              name=hparam_name))
            elif cfg['HPARAM_SEARCH'][model_name][hparam_name]['TYPE'] == 'int_uniform':
                dimensions.append(Integer(low=cfg['HPARAM_SEARCH'][model_name][hparam_name]['RANGE'][0],
                                          high=cfg['HPARAM_SEARCH'][model_name][hparam_name]['RANGE'][1],
                                          prior='uniform', name=hparam_name))
            elif cfg['HPARAM_SEARCH'][model_name][hparam_name]['TYPE'] == 'float_log':
                dimensions.append(Real(low=cfg['HPARAM_SEARCH'][model_name][hparam_name]['RANGE'][0],
                                       high=cfg['HPARAM_SEARCH'][model_name][hparam_name]['RANGE'][1],
                                       prior='log-uniform', name=hparam_name))
            elif cfg['HPARAM_SEARCH'][model_name][hparam_name]['TYPE'] == 'float_uniform':
                dimensions.append(Real(low=cfg['HPARAM_SEARCH'][model_name][hparam_name]['RANGE'][0],
                                       high=cfg['HPARAM_SEARCH'][model_name][hparam_name]['RANGE'][1],
                                       prior='uniform', name=hparam_name))
            default_params.append(cfg['HPARAMS'][model_name][hparam_name])
            hparam_names.append(hparam_name)
            results[hparam_name] = []
    print("Hyperparameter list: {}".format(hparam_names))
    init_results = results

    def objective(vals):
        hparams = dict(zip(hparam_names, vals))
        for hparam in cfg['HPARAMS'][model_name]:
            if hparam not in hparams:
                hparams[hparam] = cfg['HPARAMS'][model_name][hparam]  # Add hyperparameters being held constant
        print('HPARAM VALUES: ', hparams)
        test_metrics, _ = train_single(hparams=hparams, save_weights=False, write_logs=False, save_partitions=False)
        score = 1. - test_metrics['accuracy']
        tf.keras.backend.clear_session()
        save_hparam_search_results(init_results, score, objective_metric, hparam_names, hparams, model_name, cur_datetime)
        return score   # We aim to minimize error

    search_results = gp_minimize(func=objective, dimensions=dimensions, acq_func='EI',
                                 n_calls=cfg['HPARAM_SEARCH']['N_TRIALS'], verbose=True)
    print("Results of hyperparameter search: {}".format(search_results))
    plot_bayesian_hparam_opt(model_name, hparam_names, search_results, save_fig=True)
    return search_results

def cross_validation(frame_df=None, hparams=None, write_logs=False, save_weights=False):
    '''
    Perform k-fold cross-validation. Results are saved in CSV format.
    :param frame_df: A DataFrame consisting of the entire dataset of LUS frames
    :param save_weights: Flag indicating whether to save model weights
    :return DataFrame of metrics
    '''

    gpus = tf.config.list_physical_devices('GPU')
    if gpus:
        for gpu in gpus:
            tf.config.experimental.set_virtual_device_configuration(gpu, [
                tf.config.experimental.VirtualDeviceConfiguration(memory_limit=cfg['TRAIN']['MEMORY_LIMIT'])])

    n_classes = len(cfg['DATA']['CLASSES'])

    n_folds = cfg['TRAIN']['N_FOLDS']
    if frame_df is None:
        frame_df = pd.read_csv(cfg['PATHS']['FRAMES_TABLE'])[:5000]

    metrics = ['accuracy', 'auc', 'f1score']
    metrics += ['precision_' + c for c in cfg['DATA']['CLASSES']]
    metrics += ['recall_' + c for c in cfg['DATA']['CLASSES']]
    metrics_df = pd.DataFrame(np.zeros((n_folds + 2, len(metrics) + 1)), columns=['Fold'] + metrics)
    metrics_df['Fold'] = list(range(n_folds)) + ['mean', 'std']

    model_name = cfg['TRAIN']['MODEL_DEF'].lower()
    model_def, preprocessing_fn = get_model(model_name)
    hparams = cfg['HPARAMS'][model_name.upper()] if hparams is None else hparams

    if write_logs:
        log_dir = os.path.join(cfg['PATHS']['LOGS'], datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    else:
        log_dir = None

    all_pts = frame_df['Patient'].unique()
    val_split = 1.0 / n_folds
    pt_k_fold = KFold(n_splits=n_folds, shuffle=True)

    cur_date = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    partition_path = os.path.join(cfg['PATHS']['PARTITIONS_DIR'], 'kfold' + cur_date)
    if not os.path.exists(partition_path):
        os.makedirs(partition_path)

    # Train a model n_folds times with different folds
    cur_fold = 0
    row_idx = 0
    for train_index, test_index in pt_k_fold.split(all_pts):
        print('Fitting model for fold ' + str(cur_fold))

        # Partition into training, validation and test sets for this fold
        trainval_pts = all_pts[train_index]
        train_pts, val_pts = train_test_split(trainval_pts, test_size=val_split)
        test_pts = all_pts[test_index]
        train_df = frame_df[frame_df['Patient'].isin(train_pts)]
        val_df = frame_df[frame_df['Patient'].isin(val_pts)]
        test_df = frame_df[frame_df['Patient'].isin(test_pts)]
        train_df.to_csv(os.path.join(partition_path, 'fold_' + str(cur_fold) + '_train_set.csv'))
        val_df.to_csv(os.path.join(partition_path, 'fold_' + str(cur_fold) + '_val_set.csv'))
        test_df.to_csv(os.path.join(partition_path, 'fold_' + str(cur_fold) + '_test_set.csv'))

        # Train the model and evaluate performance on test set
        log_dir_fold = log_dir
        if write_logs:
            log_dir_fold = log_dir + 'fold' + str(cur_fold)
        model, test_metrics, _ = train_model(model_def, preprocessing_fn, train_df, val_df, test_df, hparams,
                                             save_weights=save_weights, log_dir=log_dir_fold)

        metrics_df['accuracy'] = metrics_df['accuracy'].astype(object)

        for metric in test_metrics:
            if metric in metrics_df.columns:
                metrics_df[metric][row_idx] = test_metrics[metric]
        row_idx += 1
        cur_fold += 1
        gc.collect()
        tf.keras.backend.clear_session()
        del model

    # Record mean and standard deviation of test set results
    for metric in metrics:
        metrics_df[metric][n_folds] = metrics_df[metric][0:-2].mean()

        if metric == 'f1score':
            f1_reshape = np.vstack(metrics_df[metric][0:-2])
            metrics_df[metric][n_folds + 1] = f1_reshape.std(axis=0, ddof=1)

        else:
            metrics_df[metric][n_folds + 1] = metrics_df[metric][0:-2].std()

    # Save results
    file_path = cfg['PATHS']['EXPERIMENTS'] + 'cross_val_' + model_name + \
                datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + '.csv'
    metrics_df.to_csv(file_path, columns=metrics_df.columns, index_label=False, index=False)
    return metrics_df

def train_experiment(experiment='single_train', save_weights=False, write_logs=False):
    '''
    Run a training experiment
    :param experiment: String defining which experiment to run
    :param save_weights: Flag indicating whether to save any models trained during the experiment
    :param write_logs: Flag indicating whether to write logs for training
    '''

    # Conduct the desired train experiment
    if experiment == 'single_train':
        train_single(save_weights=save_weights, write_logs=write_logs)
    elif experiment == 'hparam_search':
        bayesian_hparam_optimization()
    elif experiment == 'cross_validation':
        cross_validation(save_weights=save_weights, write_logs=write_logs)
    else:
        raise Exception("Invalid entry in TRAIN > EXPERIMENT_TYPE field of config.yml.")
    return


if __name__=='__main__':
    train_experiment(cfg['TRAIN']['EXPERIMENT_TYPE'], write_logs=True, save_weights=True)
