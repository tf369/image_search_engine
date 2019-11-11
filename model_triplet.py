from random import choice, sample

import cv2
import numpy as np

import json
from nltk.tokenize import word_tokenize
import re
import tensorflow.keras.backend as K
from tensorflow.keras.applications import ResNet50
from tensorflow.keras.applications.imagenet_utils import preprocess_input
from tensorflow.keras.layers import Input, GlobalMaxPool2D, GlobalMaxPool1D, Dense, Embedding, GRU, \
    Bidirectional, Concatenate, Lambda
from tensorflow.keras.models import Model
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.callbacks import ModelCheckpoint, ReduceLROnPlateau

UNK_TOKEN = "unk"
SEP = " sep "
img_shape = (222, 171, 3)
vec_dim = 50
BATCH_SIZE = 32



def tokenize(x):
    x = re.sub('([\\\'".!?,-/])', r' \1 ', x)
    x = re.sub('(\d+)', r' \1 ', x)

    x = word_tokenize(x.lower())

    return x


def triplet_loss(y_true, y_pred, alpha=0.4):
    """
    https://github.com/KinWaiCheuk/Triplet-net-keras/blob/master/Triplet%20NN%20Test%20on%20MNIST.ipynb
    Implementation of the triplet loss function
    Arguments:
    y_true -- true labels, required when you define a loss in Keras, you don't need it in this function.
    y_pred -- python list containing three objects:
            anchor -- the encodings for the anchor data
            positive -- the encodings for the positive data (similar to anchor)
            negative -- the encodings for the negative data (different from anchor)
    Returns:
    loss -- real number, value of the loss
    """

    total_lenght = y_pred.shape.as_list()[-1]
    anchor = y_pred[:, 0:int(total_lenght * 1 / 3)]
    positive = y_pred[:, int(total_lenght * 1 / 3):int(total_lenght * 2 / 3)]
    negative = y_pred[:, int(total_lenght * 2 / 3):int(total_lenght * 3 / 3)]

    # distance between the anchor and the positive
    pos_dist = K.sum(K.square(anchor - positive), axis=1)

    # distance between the anchor and the negative
    neg_dist = K.sum(K.square(anchor - negative), axis=1)

    # compute loss
    basic_loss = pos_dist - neg_dist + alpha
    loss = K.maximum(basic_loss, 0.0)

    return loss


def map_sentence(tokenized_sentence, mapping):
    out_sentence = list(map(lambda x: mapping[x if x in mapping else UNK_TOKEN], tokenized_sentence))
    return out_sentence


def map_sentences(list_tokenized_sentences, mapping):
    mapped = []
    for sentence in list_tokenized_sentences:
        out_sentence = map_sentence(sentence, mapping)
        mapped.append(out_sentence)

    return mapped


def cap_sequence(seq, max_len, append):
    if len(seq) < max_len:
        if np.random.uniform(0, 1) < 0.5:
            return seq + [append] * (max_len - len(seq))
        else:
            return [append] * (max_len - len(seq)) + seq
    else:
        if np.random.uniform(0, 1) < 0.5:

            return seq[:max_len]
        else:
            return seq[-max_len:]


def cap_sequences(list_sequences, max_len, append):
    capped = []
    for seq in list_sequences:
        out_seq = cap_sequence(seq, max_len, append)
        capped.append(out_seq)

    return capped


def read_img(path):
    img = cv2.imread(path)
    img = cv2.resize(img, (171, 222))
    return preprocess_input(img)


def gen(list_images, list_captions, batch_size=16):
    indexes = list(range(len(list_images)))
    while True:
        batch_indexes = sample(indexes, batch_size)

        candidate_images = [list_images[i] for i in batch_indexes]
        captions_p = [list_captions[i] for i in batch_indexes]

        captions_n = [choice(list_captions) for _ in batch_indexes]

        X1 = np.array([read_img(x) for x in candidate_images])
        X2 = np.array(captions_p)
        X3 = np.array(captions_n)

        yield [X1, X2, X3], np.zeros((batch_size, 3*vec_dim))


def pretrain_model(vocab_size, lr=0.0001):
    input_1 = Input(shape=(None, None, 3))
    input_2 = Input(shape=(None,))
    input_3 = Input(shape=(None,))

    base_model = ResNet50(weights='imagenet', include_top=False)

    x1 = base_model(input_1)
    x1 = GlobalMaxPool2D()(x1)

    dense_1 = Dense(vec_dim, activation="linear", name="dense_image_1")

    x1 = dense_1(x1)

    embed = Embedding(vocab_size, 50, name="embed")
    gru = Bidirectional(GRU(256, return_sequences=True), name="gru_1")
    dense_2 = Dense(vec_dim, activation ="linear", name="dense_text_1")

    x2 = embed(input_2)
    x2 = gru(x2)
    x2 = GlobalMaxPool1D()(x2)
    x2 = dense_2(x2)

    x3 = embed(input_3)
    x3 = gru(x3)
    x3 = GlobalMaxPool1D()(x3)
    x3 = dense_2(x3)

    _norm = Lambda(lambda x: K.l2_normalize(x, axis=-1))

    x1 = _norm(x1)
    x2 = _norm(x2)
    x3 = _norm(x3)

    x = Concatenate(axis=-1)([x1, x2, x3])

    model = Model([input_1, input_2, input_3], x)

    model.compile(loss=triplet_loss, optimizer=Adam(lr))

    model.summary()

    return model


mapping = json.load(open('mapping.json', 'r'))

train = json.load(open("amazon_filtred_train_data.json", 'r'))
val = json.load(open("amazon_filtred_train_data.json", 'r'))

list_images_train, captions_train = list(zip(*train))
captions_train = [tokenize(x) for x in captions_train]
captions_train = map_sentences(captions_train, mapping)
captions_train = cap_sequences(captions_train, 150, 0)

list_images_val, captions_val = list(zip(*val))
captions_val = [tokenize(x) for x in captions_val]

captions_val = map_sentences(captions_val, mapping)
captions_val = cap_sequences(captions_val, 150, 0)

file_path = "pretrain_model_triplet.h5"

model = pretrain_model(vocab_size=len(mapping) + 1)

# model.load_weights(file_path, by_name=True)

checkpoint = ModelCheckpoint(file_path, monitor='val_loss', verbose=1, save_best_only=True, mode='min')
reduce = ReduceLROnPlateau(monitor="val_loss", mode='min', patience=10, min_lr=1e-7)

model.fit_generator(gen(list_images_train, captions_train, batch_size=BATCH_SIZE), use_multiprocessing=True,
                    validation_data=gen(list_images_train, captions_val, batch_size=BATCH_SIZE), epochs=10000,
                    verbose=1, workers=4, steps_per_epoch=200, validation_steps=100, callbacks=[checkpoint, reduce])
model.save_weights(file_path)
