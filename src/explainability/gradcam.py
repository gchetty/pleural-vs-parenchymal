import os

import yaml
import cv2
import numpy as np
import pandas as pd
from keras_preprocessing.image import ImageDataGenerator
from tqdm import tqdm
import tensorflow as tf
from tkinter import filedialog as fd
from tensorflow.keras.models import load_model
from tensorflow.keras.models import Model

from src.predict import predict_set
from src.visualization.visualization import visualize_heatmap
from src.models.models import get_model
from src.data.preprocessor import Preprocessor

cfg = yaml.full_load(open(os.getcwd() + "/config.yml", 'r'))

EPSILON = np.finfo('float').eps

class GradCAMExplainer:

    def __init__(self):
        self.model = load_model(cfg['PATHS']['MODEL_TO_LOAD'], compile=False)
        self.save_img_dir = cfg['PATHS']['HEATMAPS']
        self.frames_dir = cfg['PATHS']['FRAMES_DIR']
        self.img_dim = tuple(cfg['DATA']['IMG_DIM'])
        self.classes = cfg['DATA']['CLASSES']
        self.x_col = 'Frame Path'
        self.y_col = 'Class Name'
        _, preprocessing_fn = get_model(cfg['TRAIN']['MODEL_DEF'])
        self.preprocessing_fn = preprocessing_fn
        self.heatmap_gen_fn = self.get_heatmap_gen_fn(cfg['EXPLAINABILITY']['GRAD_CAM']['MODE'])

        # Get name of final convolutional layer
        layer_name = ''
        for layer in self.model.layers:
            if any('Conv' in l for l in layer._keras_api_names):
                layer_name = layer.name
        self.last_conv_layer = layer_name
        self.hm_intensity = 0.5

    def get_heatmap_gen_fn(self, mode):
        if mode == 'plusplus':
            return self.get_gradcam_plusplus_heatmap
        elif mode == 'normal':
            return self.get_gradcam_heatmap

    @staticmethod
    def get_gradcam_heatmap(model, img):
        '''
        Compute the raw heatmap for the given image using the original Grad-CAM formula for linearization weights.
        :param model: Model to process given image through
        :param img: Preprocessed image in dataset
        :return: Unactivated, unnormalized heatmap
        '''
        # Obtain gradient of output with respect to last convolutional layer weights
        with tf.GradientTape() as tape:
            model_out, last_conv_layer = model(img)
            class_out = tf.math.maximum(1 - model_out[:, np.argmax(model_out[0])], model_out[:, np.argmax(model_out[0])])
            grads = tape.gradient(class_out, last_conv_layer)
            pooled_grads = tf.keras.backend.mean(grads, axis=(0, 1, 2))

        heatmap = tf.reduce_mean(tf.multiply(pooled_grads, last_conv_layer), axis=-1)

        return

    @staticmethod
    def get_gradcam_plusplus_heatmap(model, img):
        '''
        Compute the raw heatmap for the given image using the Grad-CAM++ formula for linearization weights.
        :param model: Model to process given image through
        :param img: Preprocessed image in dataset
        :return: Unactivated, unnormalized heatmap
        '''
        # Obtain first, second, and third gradients of output with respect to last convolutional layer weights
        with tf.GradientTape() as tape_3:
            with tf.GradientTape() as tape_2:
                with tf.GradientTape() as tape_1:
                    model_out, last_conv_layer = model(img)
                    class_out = tf.math.maximum(1 - model_out[:, np.argmax(model_out[0])],
                                                model_out[:, np.argmax(model_out[0])])
                    first_grads = tape_1.gradient(class_out, last_conv_layer)
                second_grads = tape_2.gradient(first_grads, last_conv_layer)
            third_grads = tape_3.gradient(second_grads, last_conv_layer)

        last_conv_sum = tf.reduce_sum(last_conv_layer, axis=(0, 1, 2))

        alpha_num = second_grads
        alpha_denom = second_grads * 2.0 + third_grads * last_conv_sum
        alpha_denom = np.where(alpha_denom != 0.0, alpha_denom, EPSILON)

        alphas = alpha_num / alpha_denom
        alpha_norm = np.sum(alphas, axis=(1, 2))
        alphas /= alpha_norm
        activated_first_grad = np.maximum(first_grads, 0.0)  # Passing through ReLU
        linearization_weights = np.sum(activated_first_grad * alphas, axis=(1, 2))

        heatmap = np.sum(linearization_weights * last_conv_layer, axis=-1)

        return heatmap

    def apply_gradcam(self, frame_df):
        '''
        For each image in the dataset provided, make a prediction and overlay a heatmap depicting the gradient of the
        predicted class with respect to the feature maps of the final convolutional layer of the model.
        :param frame_df: Pandas Dataframe of LUS frames, linking image filenames to labels
        '''

        test_img_gen = ImageDataGenerator(preprocessing_function=self.preprocessing_fn)
        test_generator = test_img_gen.flow_from_dataframe(dataframe=frame_df, directory=self.frames_dir,
                                                          x_col=self.x_col, y_col=self.y_col, target_size=self.img_dim,
                                                          batch_size=1, class_mode='categorical',
                                                          validate_filenames=False, shuffle=False)

        preds, probs = predict_set(self.model, self.preprocessing_fn, frame_df)

        for idx in tqdm(range(probs.shape[0])):

            # Get idx'th preprocessed image in the  dataset
            x, y = test_generator.next()

            # Get the corresponding original image (no preprocessing)
            orig_img = cv2.imread(os.path.join(self.frames_dir, frame_df[self.x_col].iloc[idx]))
            orig_img = cv2.resize(orig_img, self.img_dim, interpolation=cv2.INTER_NEAREST)  # Resize image

            last_conv_layer = self.model.get_layer(self.last_conv_layer)
            iterate = Model([self.model.inputs], [self.model.output, last_conv_layer.output])

            raw_heatmap = self.heatmap_gen_fn(iterate, x)

            # Upsample and overlay heatmap onto original image
            heatmap = np.maximum(raw_heatmap, 0.0)  # Equivalent of passing through ReLU
            heatmap /= np.max(heatmap) if np.max(heatmap) != 0 else EPSILON
            heatmap = heatmap.squeeze(axis=0)
            heatmap = cv2.resize(heatmap, self.img_dim)
            heatmap = cv2.applyColorMap(255 - np.uint8(255 * heatmap), cv2.COLORMAP_JET)
            heatmap_img = cv2.addWeighted(heatmap, self.hm_intensity, orig_img, 1.0 - self.hm_intensity, 0)

            # Visualize the Grad-CAM heatmap and optionally save it to disk
            img_filename = frame_df[self.x_col].iloc[idx]
            label = frame_df['Class'].iloc[idx]
            _ = visualize_heatmap(orig_img, heatmap_img, img_filename, label, probs[idx], self.classes,
                                  dir_path=self.save_img_dir)
        return heatmap


    def get_heatmap_for_frame(self, frame_df=None):
        '''
        Apply Grad-CAM to an individual LUS image
        :param frame_df: Pandas DataFrame of LUS frames
        :return: The heatmap produced by Grad-CAM
        '''

        file_path = fd.askopenfilename(initialdir=cfg['PATHS']['FRAMES_DIR'], title='Select a Frame',
                                       filetypes=[('jpeg files', '*.jpg')])

        if frame_df is None:
            frame_df = pd.read_csv(cfg['PATHS']['FRAMES_TABLE'])

        filtered_df = frame_df[frame_df[self.x_col] == os.path.basename(file_path)]
        filtered_df.reset_index(inplace=True)

        heatmap = self.apply_gradcam(filtered_df)
        return heatmap



if __name__ == '__main__':
    gradcam = GradCAMExplainer()
    while True:
        gradcam.get_heatmap_for_frame()