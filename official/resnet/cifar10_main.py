# Copyright 2017 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
import os
import sys

import numpy as np
import tensorflow as tf

import resnet_model

HEIGHT = 32
WIDTH = 32
DEPTH = 3
NUM_CLASSES = 10
NUM_DATA_BATCHES = 5
NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN = 10000 * NUM_DATA_BATCHES
NUM_EXAMPLES_PER_EPOCH_FOR_EVAL = 10000

parser = argparse.ArgumentParser()

# Basic model parameters.
parser.add_argument('--data_dir', type=str, default='/tmp/cifar10_data',
                    help='The path to the CIFAR-10 data directory.')

parser.add_argument('--model_dir', type=str, default='/tmp/cifar10_model',
                    help='The directory where the model will be stored.')

parser.add_argument('--resnet_size', type=int, default=32,
                    help='The size of the ResNet model to use.')

parser.add_argument('--train_steps', type=int, default=100000,
                    help='The number of batches to train.')

parser.add_argument('--steps_per_eval', type=int, default=4000,
                    help='The number of batches to run in between evaluations.')

parser.add_argument('--batch_size', type=int, default=128,
                    help='The number of images per batch.')

FLAGS = parser.parse_args()

# Scale the learning rate linearly with the batch size. When the batch size is
# 128, the learning rate should be 0.1.
_INITIAL_LEARNING_RATE = 0.1 * FLAGS.batch_size / 128
_MOMENTUM = 0.9

# We use a weight decay of 0.0002, which performs better than the 0.0001 that
# was originally suggested.
_WEIGHT_DECAY = 2e-4

_BATCHES_PER_EPOCH = NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN / FLAGS.batch_size


def record_dataset(filenames):
  """Returns an input pipeline Dataset from `filenames`."""
  record_bytes = HEIGHT * WIDTH * DEPTH + 1
  return tf.contrib.data.FixedLengthRecordDataset(filenames, record_bytes)


def filenames(mode):
  """Returns a list of filenames based on 'mode'."""
  data_dir = os.path.join(FLAGS.data_dir, 'cifar-10-batches-bin')

  assert os.path.exists(data_dir), ('Run cifar10_download_and_extract.py first '
      'to download and extract the CIFAR-10 data.')

  if mode == tf.estimator.ModeKeys.TRAIN:
    return [
        os.path.join(data_dir, 'data_batch_%d.bin' % i)
        for i in range(1, NUM_DATA_BATCHES + 1)
    ]
  elif mode == tf.estimator.ModeKeys.EVAL:
    return [os.path.join(data_dir, 'test_batch.bin')]
  else:
    raise ValueError('Invalid mode: %s' % mode)


def dataset_parser(value):
  """Parse a CIFAR-10 record from value."""
  # Every record consists of a label followed by the image, with a fixed number
  # of bytes for each.
  label_bytes = 1
  image_bytes = HEIGHT * WIDTH * DEPTH
  record_bytes = label_bytes + image_bytes

  # Convert from a string to a vector of uint8 that is record_bytes long.
  raw_record = tf.decode_raw(value, tf.uint8)

  # The first byte represents the label, which we convert from uint8 to int32.
  label = tf.cast(raw_record[0], tf.int32)

  # The remaining bytes after the label represent the image, which we reshape
  # from [depth * height * width] to [depth, height, width].
  depth_major = tf.reshape(raw_record[label_bytes:record_bytes],
                           [DEPTH, HEIGHT, WIDTH])

  # Convert from [depth, height, width] to [height, width, depth], and cast as
  # float32.
  image = tf.cast(tf.transpose(depth_major, [1, 2, 0]), tf.float32)

  return image, tf.one_hot(label, NUM_CLASSES)


def train_preprocess_fn(image, label):
  """Preprocess a single training image of layout [height, width, depth]."""
  # Resize the image to add four extra pixels on each side.
  image = tf.image.resize_image_with_crop_or_pad(image, HEIGHT + 8, WIDTH + 8)

  # Randomly crop a [HEIGHT, WIDTH] section of the image.
  image = tf.random_crop(image, [HEIGHT, WIDTH, DEPTH])

  # Randomly flip the image horizontally.
  image = tf.image.random_flip_left_right(image)

  return image, label


def input_fn(mode, batch_size):
  """Input_fn using the contrib.data input pipeline for CIFAR-10 dataset.

  Args:
    mode: Standard names for model modes (tf.estimators.ModeKeys).
    batch_size: The number of samples per batch of input requested.
  """
  dataset = record_dataset(filenames(mode))

  # For training repeat forever.
  if mode == tf.estimator.ModeKeys.TRAIN:
    dataset = dataset.repeat()

  dataset = dataset.map(dataset_parser, num_threads=1,
                        output_buffer_size=2 * batch_size)

  # For training, preprocess the image and shuffle.
  if mode == tf.estimator.ModeKeys.TRAIN:
    dataset = dataset.map(train_preprocess_fn, num_threads=1,
                          output_buffer_size=2 * batch_size)

    # Ensure that the capacity is sufficiently large to provide good random
    # shuffling.
    buffer_size = int(NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN * 0.4) + 3 * batch_size
    dataset = dataset.shuffle(buffer_size=buffer_size)

  # Subtract off the mean and divide by the variance of the pixels.
  dataset = dataset.map(
      lambda image, label: (tf.image.per_image_standardization(image), label),
      num_threads=1,
      output_buffer_size=2 * batch_size)

  # Batch results by up to batch_size, and then fetch the tuple from the
  # iterator.
  iterator = dataset.batch(batch_size).make_one_shot_iterator()
  images, labels = iterator.get_next()

  return images, labels


def cifar10_model_fn(features, labels, mode):
  """Model function for CIFAR-10."""
  tf.summary.image('images', features, max_outputs=6)

  network = resnet_model.cifar10_resnet_v2_generator(
      FLAGS.resnet_size, NUM_CLASSES)

  inputs = tf.reshape(features, [-1, HEIGHT, WIDTH, DEPTH])
  logits = network(inputs, mode == tf.estimator.ModeKeys.TRAIN)

  predictions = {
      'classes': tf.argmax(logits, axis=1),
      'probabilities': tf.nn.softmax(logits, name='softmax_tensor')
  }

  if mode == tf.estimator.ModeKeys.PREDICT:
    return tf.estimator.EstimatorSpec(mode=mode, predictions=predictions)

  # Calculate loss, which includes softmax cross entropy and L2 regularization.
  cross_entropy = tf.losses.softmax_cross_entropy(
      logits=logits, onehot_labels=labels)

  # Create a tensor named cross_entropy for logging purposes.
  tf.identity(cross_entropy, name='cross_entropy')
  tf.summary.scalar('cross_entropy', cross_entropy)

  # Add weight decay to the loss.
  loss = cross_entropy + _WEIGHT_DECAY * tf.add_n(
      [tf.nn.l2_loss(v) for v in tf.trainable_variables()])

  if mode == tf.estimator.ModeKeys.TRAIN:
    global_step = tf.train.get_or_create_global_step()

    # Multiply the learning rate by 0.1 at 100, 150, and 200 epochs.
    boundaries = [int(_BATCHES_PER_EPOCH * epoch) for epoch in [100, 150, 200]]
    values = [_INITIAL_LEARNING_RATE * decay for decay in [1, 0.1, 0.01, 0.001]]
    learning_rate = tf.train.piecewise_constant(
        tf.cast(global_step, tf.int32), boundaries, values)

    # Create a tensor named learning_rate for logging purposes
    tf.identity(learning_rate, name='learning_rate')
    tf.summary.scalar('learning_rate', learning_rate)

    optimizer = tf.train.MomentumOptimizer(
        learning_rate=learning_rate,
        momentum=_MOMENTUM)

    # Batch norm requires update ops to be added as a dependency to the train_op
    update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
    with tf.control_dependencies(update_ops):
      train_op = optimizer.minimize(loss, global_step)
  else:
    train_op = None

  accuracy= tf.metrics.accuracy(
      tf.argmax(labels, axis=1), predictions['classes'])
  metrics = {'accuracy': accuracy}

  # Create a tensor named train_accuracy for logging purposes
  tf.identity(accuracy[1], name='train_accuracy')
  tf.summary.scalar('train_accuracy', accuracy[1])

  return tf.estimator.EstimatorSpec(
      mode=mode,
      predictions=predictions,
      loss=loss,
      train_op=train_op,
      eval_metric_ops=metrics)


def main(unused_argv):
  # Using the Winograd non-fused algorithms provides a small performance boost.
  os.environ['TF_ENABLE_WINOGRAD_NONFUSED'] = '1'

  cifar_classifier = tf.estimator.Estimator(
      model_fn=cifar10_model_fn, model_dir=FLAGS.model_dir)

  for cycle in range(FLAGS.train_steps // FLAGS.steps_per_eval):
    tensors_to_log = {
        'learning_rate': 'learning_rate',
        'cross_entropy': 'cross_entropy',
        'train_accuracy': 'train_accuracy'
    }

    logging_hook = tf.train.LoggingTensorHook(
        tensors=tensors_to_log, every_n_iter=100)

    cifar_classifier.train(
        input_fn=lambda: input_fn(tf.estimator.ModeKeys.TRAIN,
                                  batch_size=FLAGS.batch_size),
        steps=FLAGS.steps_per_eval,
        hooks=[logging_hook])

    # Evaluate the model and print results
    eval_results = cifar_classifier.evaluate(
        input_fn=lambda: input_fn(tf.estimator.ModeKeys.EVAL,
                                  batch_size=FLAGS.batch_size))
    print(eval_results)


if __name__ == '__main__':
  tf.logging.set_verbosity(tf.logging.INFO)
  tf.app.run()
