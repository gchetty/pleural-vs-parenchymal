import datetime
import os
import io

import matplotlib.pyplot as plt
import matplotlib as mpl
import tensorflow as tf
import seaborn as sns
from sklearn.metrics import confusion_matrix, roc_curve
import numpy as np
import yaml
from pandas.api.types import is_numeric_dtype

mpl.rcParams['figure.figsize'] = (12, 8)
cfg = yaml.full_load(open(os.getcwd() + "/config.yml", 'r'))

def plot_to_tensor():
    '''
    Converts a matplotlib figure to an image tensor
    :param figure: A matplotlib figure
    :return: Tensorflow tensor representing the matplotlib image
    '''
    # Save the plot to a PNG in memory.
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)

    image_tensor = tf.image.decode_png(buf.getvalue(), channels=4)     # Convert .png buffer to tensorflow image
    image_tensor = tf.expand_dims(image_tensor, 0)     # Add the batch dimension
    return image_tensor

def plot_roc(labels, predictions, class_name_list, dir_path=None, title=None):
    '''
    Plots the ROC curve for predictions on a dataset
    :param labels: Ground truth labels
    :param predictions: Model predictions corresponding to the labels
    :param class_name_list: Ordered list of class names
    :param dir_path: Directory in which to save image
    '''
    plt.clf()
    # for class_id in range(len(class_name_list)):
    #     class_name = class_name_list[class_id]
    #     single_class_preds = predictions[:, class_id]    # Only care about one class
    #     single_class_labels = (np.array(labels) == class_id) * 1.0
    fp, tp, _ = roc_curve(labels, predictions)  # Get values for true positive and true negative
    plt.plot(100*fp, 100*tp, linewidth=2)   # Plot the ROC curve

    if title is None:
        plt.title('ROC curves for test set')
    else:
        plt.title(title)
    plt.xlabel('False positives [%]')
    plt.ylabel('True positives [%]')
    plt.xlim([-5,105])
    plt.ylim([-5,105])
    plt.grid(True)
    plt.legend()
    ax = plt.gca()
    ax.set_aspect('equal')
    if dir_path is not None:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
        plt.savefig(dir_path + 'ROC_' + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + '.png')
    return plt

def plot_confusion_matrix(labels, predictions, class_name_list, dir_path=None, title=None):
    '''
    Plot a confusion matrix for the ground truth labels and corresponding model predictions for a particular class.
    :param labels: Ground truth labels
    :param predictions: Model predictions
    :param class_name_list: Ordered list of class names
    :param dir_path: Directory in which to save image
    '''
    plt.clf()
    #predictions = list(np.argmax(predictions, axis=1))
    predictions = np.round(predictions)
    ax = plt.subplot()
    cm = confusion_matrix(list(labels), predictions)  # Determine confusion matrix
    im = ax.imshow(cm, interpolation='nearest', cmap=plt.cm.Blues)  # Plot confusion matrix
    ax.figure.colorbar(im, ax=ax)
    ax.set(yticklabels=class_name_list, xticklabels=class_name_list)
    ax.xaxis.set_major_locator(mpl.ticker.IndexLocator(base=1, offset=0.5))
    ax.yaxis.set_major_locator(mpl.ticker.IndexLocator(base=1, offset=0.5))

    # Print the confusion matrix numbers in the center of each cell of the plot
    thresh = cm.max() / 2
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            plt.text(j, i, cm[i, j], horizontalalignment="center", color="white" if cm[i, j] > thresh else "black")

    # Set plot's title and axis names
    if title is None:
        plt.title('Confusion matrix for test set')
    else:
        plt.title(title)
    plt.ylabel('Actual label')
    plt.xlabel('Predicted label')

    # Save the image
    if dir_path is not None:
        plt.savefig(dir_path + 'CM_' + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + '.png')

    print('Confusion matrix: ', cm)    # Print the confusion matrix
    return plt

def visualize_heatmap(orig_img, heatmap, img_filename, label, prob, class_names, dir_path=None):
    '''
    Obtain a comparison of an original image and heatmap produced by Grad-CAM.
    :param orig_img: Original X-Ray image
    :param heatmap: Heatmap generated by Grad-CAM.
    :param img_filename: Filename of the image explained
    :param label: Ground truth class of the example
    :param probs: Prediction probabilities
    :param class_names: Ordered list of class names
    :param dir_path: Path to save the generated image
    :return: Path to saved image
    '''

    fig, ax = plt.subplots(1, 2)
    ax[0].imshow(orig_img)
    ax[1].imshow(heatmap)

    # Display some information about the example
    pred_class = np.round(prob)
    fig.text(0.02, 0.90, "Prediction probability: " + str(prob), fontsize=10)
    fig.text(0.02, 0.92, "Predicted Class: " + str(int(pred_class)) + ' (' + class_names[int(pred_class)] + ')', fontsize=10)
    if label is not None:
        fig.text(0.02, 0.94, "Ground Truth Class: " + str(label) + ' (' + class_names[label] + ')', fontsize=10)
    fig.suptitle("Grad-CAM heatmap for image " + img_filename, fontsize=8, fontweight='bold')
    fig.tight_layout()

    # Save the image
    filename = None
    if dir_path is not None:
        filename = os.path.join(dir_path, img_filename.split('/')[-1] + '_gradcam_' + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + '.png')
        plt.savefig(filename)
    return filename


def plot_clip_pred_threshold_experiment_old(metrics_df, var_col, metrics_to_plot=None,
                                        ax=None, im_path=None, title=None, x_label=None):
    '''
    Visualizes the Plot classification metrics for clip predictions over various B-line count thresholds.
    :param metrics_df: DataFrame containing classification metrics for different. The first column should be the
                       various B-line thresholds and the rest are classification metrics
    :min_threshold: Minimum B-line threshold
    :max_threshold: Maximum B-line threshold
    :thresh_col: Column of DataFrame corresponding to threshold variable
    :class_thresh: Classification threshold
    :metrics_to_plot: List of metrics to include on the plot
    :ax: Matplotlib subplot
    :im_path: Path in which to save image
    :title: Plot title
    :x_label: X-label for plot
    '''
    min_threshold = metrics_df[var_col][0]
    max_threshold = metrics_df[var_col][len(metrics_df[var_col]) - 1]

    min_y_lim = 1.0
    max_y_lim = 0.0
    for x in metrics_to_plot:
        min = metrics_df[x].min()
        max = metrics_df[x].max()
        min_y_lim = min if min < min_y_lim else min_y_lim
        max_y_lim = max if max < max_y_lim else max_y_lim

    if ax is None:
        ax = plt.subplot()
    if title:
        plt.title(title)
    if x_label:
        ax.set_xlabel(var_col)

    if metrics_to_plot is None:
        metric_names = [m for m in metrics_df.columns if m != var_col and is_numeric_dtype(metrics_df[m])]
    else:
        metric_names = metrics_to_plot

    # Plot each metric as a separate series and place a legend
    for metric_name in metric_names:
        if is_numeric_dtype(metrics_df[metric_name]):
            ax.plot(metrics_df[var_col], metrics_df[metric_name])

    # Change axis ticks and add grid
    #ax.minorticks_on()
    # for tick in ax.get_xticklabels():
    #     tick.set_color('gray')
    # for tick in ax.get_yticklabels():
    #     tick.set_color('gray')
    ax.set_xlim(min_threshold - 1, max_threshold + 1)
    ax.set_ylim(min_y_lim-0.02, max_y_lim+0.02)
    ax.xaxis.set_ticks(np.arange(0, max_threshold + 1, 5))
    ax.yaxis.set_ticks(np.arange(min_y_lim-0.02, max_y_lim+0.02, 0.1))
    # ax.grid(True, which='both', color='lightgrey')

    # Draw legend
    ax.legend(metric_names, loc='lower right')
    plt.show()
    if im_path:
        plt.savefig(im_path + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + '.png')
    return ax

def plot_clip_pred_experiment(metrics_df, var_col, metrics_to_plot=None,
                                        im_path=None, title=None, x_label=None,  y_label=None,
                                        model_name = None, experiment_type = None):
    '''
    Visualizes the Plot classification metrics for clip predictions over various B-line count thresholds.
    :param metrics_df: DataFrame containing classification metrics for different. The first column should be the
                       various B-line thresholds and the rest are classification metrics
    :var_col: Column of DataFrame corresponding to variable
    :metrics_to_plot: List of metrics to include on the plot
    :im_path: Path in which to save image
    :title: Plot title
    :x_label: X-label for plot
    :y_label: X-label for plot
    '''

    if metrics_to_plot is None:
        metric_names = [m for m in metrics_df.columns if m != var_col and is_numeric_dtype(metrics_df[m])]
    else:
        metric_names = metrics_to_plot

    # Plot each metric as a separate series and place a legend after clearing the plot
    plt.clf()
    for metric_name in metric_names:
        if is_numeric_dtype(metrics_df[metric_name]):
            sns.lineplot(x=metrics_df[var_col], y=metrics_df[metric_name])

    # Draw legend
    if title:
        plt.title(title)
    if x_label:
        plt.xlabel(var_col)
    if y_label:
        plt.ylabel(var_col)
    plt.legend(metric_names)
    if im_path:
        savefig_name = im_path
        if model_name:
            savefig_name  = savefig_name + model_name+"-"
        if experiment_type:
            savefig_name = savefig_name + experiment_type+"-"
        plt.savefig(savefig_name + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + '.png')
    return