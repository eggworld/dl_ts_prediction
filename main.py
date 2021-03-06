__all__ = ["Model"]

import numpy as np
from weakref import WeakKeyDictionary
import pandas as pd
from TSErrors import FindErrors
from sklearn.preprocessing import MinMaxScaler
import matplotlib.pyplot as plt
import os
from sklearn.model_selection import train_test_split
import keract_mod as keract

from models.global_variables import keras, tf
from utils import plot_results, plot_loss, maybe_create_path, save_config_file
from models.global_variables import LOSSES, ACTIVATIONS
#

KModel = keras.models.Model
layers = keras.layers

tf.compat.v1.disable_eager_execution()
# tf.enable_eager_execution()


class AttributeNotSetYet:
    def __init__(self, func_name):
        self.data = WeakKeyDictionary()
        self.func_name = func_name

    def __get__(self, instance, owner):
        raise AttributeError("run the function {} first to get {}".format(self.func_name, self.name))

    def __set_name__(self, owner, name):
        self.name = name

class AttributeStore(object):
    """ a class which will just make sure that attributes are set at its childs class level and not here.
    It's purpose is just to avoid cluttering of __init__ method of its child classes. """
    k_model = AttributeNotSetYet("`build_nn` to build neural network")
    method = None
    ins = None
    outs = None
    en_densor_We = None
    en_LSTM_cell = None
    auto_enc_composite = None
    de_LSTM_cell = None
    de_densor_We = None
    test_indices = None
    train_indices = None
    run_paras = AttributeNotSetYet("You must define the `run_paras` method first")


class Model(AttributeStore):

    def __init__(self, data_config: dict,
                 nn_config: dict,
                 data: pd.DataFrame,
                 intervals=None,
                 path: str=None):
        self.data_config = data_config
        self.nn_config = nn_config
        self.intervals = intervals
        self.data = data[data_config['inputs'] + data_config['outputs']]
        self.ins = len(self.data_config['inputs'])
        self.outs = len(self.data_config['outputs'])
        self.loss = LOSSES[self.nn_config['loss']]
        self.KModel = KModel
        self.path = maybe_create_path(path=path)

    @property
    def lookback(self):
        return self.data_config['lookback']

    @property
    def ins(self):
        return self._ins

    @ins.setter
    def ins(self, x):
        self._ins = x

    @property
    def outs(self):
        return self._outs

    @outs.setter
    def outs(self, x):
        self._outs = x

    @property
    def loss(self):
        return self._loss

    @loss.setter
    def loss(self, x):
        self._loss = x

    @property
    def KModel(self):
        """In case when we want to customize the model such as for
        implementing custom `train_step`, we can provide the customized model as input the this Model class"""
        return self._k_model

    @KModel.setter
    def KModel(self, x):
        self._k_model = x

    @property
    def layer_names(self):
        _all_layers = []
        for layer in self.k_model.layers:
            _all_layers.append(layer.name)
        return _all_layers

    def fetch_data(self, data: pd.DataFrame,  st: int, en=None,
                   shuffle: bool = True,
                   cache_data=True,
                   noise: int = 0,
                   indices: list = None):

        if indices is not None:
            assert isinstance(indices, list), "indices must be list"
            if en is not None or st != 0:
                raise ValueError

        df = data

        # # add random noise in the data
        if noise > 0:
            x = pd.DataFrame(np.random.randint(0, 1, (len(df), noise)))
            df = pd.concat([df, x], axis=1)
            prev_inputs = self.data_config['inputs']
            self.data_config['inputs'] = prev_inputs + list(x.columns)
            ys = []
            for y in self.data_config['outputs']:
                ys.append(df.pop(y))
            df[self.data_config['outputs']] = ys

        cols = self.data_config['inputs'] + self.data_config['outputs']
        df = df[cols]

        scaler = MinMaxScaler()
        data = scaler.fit_transform(df)
        df = pd.DataFrame(data)

        if en is None:
            en = df.shape[0]

        if self.intervals is None:

            df = df[st:en]
            df.columns = self.data_config['inputs'] + self.data_config['outputs']

            x, y, label = self.get_data(df,
                                        len(self.data_config['inputs']),
                                        len(self.data_config['outputs'])
                                        )
            if indices is not None:
                # if indices are given then this should be done after `get_data` method
                x = x[indices]
                y = y[indices]
                label = label[indices]
        else:
            xs, ys, labels = [], [], []
            for _st, _en in self.intervals:
                df1 = df[_st:_en]

                df.columns = self.data_config['inputs'] + self.data_config['outputs']
                df1.columns = self.data_config['inputs'] + self.data_config['outputs']

                if df1.shape[0] > 0:
                    x, y, label = self.get_data(df1, len(self.data_config['inputs']), len(self.data_config['outputs']))
                    xs.append(x)
                    ys.append(y)
                    labels.append(label)

            if indices is None:
                x = np.vstack(xs)[st:en]
                y = np.vstack(ys)[st:en]
                label = np.vstack(labels)[st:en]
            else:
                x = np.vstack(xs)[indices]
                y = np.vstack(ys)[indices]
                label = np.vstack(labels)[indices]

        # data_path = self.data_config['data_path']
        # cache_path = os.path.join(os.path.dirname(data_path), 'cache')
        #
        # if cache_data and os.path.isdir(cache_path) is False:
        #     os.mkdir(cache_path)
        # fname = os.path.join(cache_path, 'nasdaq_T{}.h5'.format(self.lookback))
        # if os.path.exists(fname) and cache_data:
        #     input_x, input_y, label_y = read_cache(fname)
        #     print('load %s successfully' % fname)
        # else:
        #     input_x, input_y, label_y = get_data(data_path, self.lookback, st, en)
        #     if cache_data:
        #         write_cache(fname, input_x, input_y, label_y)

        if shuffle:
            x, y, label = unison_shuffled_copies(x, y, label)

        print('input_X shape:', x.shape)
        print('input_Y shape:', y.shape)
        print('label_Y shape:', label.shape)

        return x, y, label

    def fit(self, inputs, outputs, **callbacks):

        _monitor = 'val_loss' if self.data_config['val_fraction'] > 0.0 else 'loss'
        model_checkpoint_callback = keras.callbacks.ModelCheckpoint(
            filepath=self.path,
            save_weights_only=True,
            monitor=_monitor,
            mode='max',
            save_best_only=True)
        _callbacks = [model_checkpoint_callback]

        _callbacks.append(keras.callbacks.EarlyStopping(
            monitor=_monitor, min_delta=self.nn_config['min_val_loss'],
            patience=self.nn_config['patience'], verbose=0, mode='auto'
        ))

        if 'tensorboard' in callbacks:
            _callbacks.append(keras.callbacks.TensorBoard(log_dir=self.path, histogram_freq=1))
        self.k_model.fit(inputs,
                         outputs,
                         epochs=self.nn_config['epochs'],
                         batch_size=self.data_config['batch_size'], validation_split=self.data_config['val_fraction'],
                         callbacks=_callbacks
                         )
        history = self.k_model.history

        self.save_config()

        return history

    def get_indices(self, indices=None):
        if isinstance(indices, str) and indices.upper() == 'RANDOM':
            if self.data_config['ignore_nans']:
                tot_obs = self.data.shape[0]
            else:
                if self.outs == 1:
                    tot_obs = self.data.shape[0] - int(self.data[self.data_config['outputs']].isna().sum())
                else:
                    raise ValueError
            idx = np.arange(tot_obs - self.lookback)
            train_indices, test_idx = train_test_split(idx, test_size=self.data_config['val_fraction'], random_state=313)
            setattr(self, 'test_indices', list(test_idx))
            indices = list(train_indices)

        setattr(self, 'train_indices', indices)

        return indices

    def run_paras(self, **kwargs):

        train_x, train_y, train_label = self.fetch_data(self.data, **kwargs)
        return train_x, train_label


    def train_nn(self, st=0, en=None, indices=None, **callbacks):

        indices = self.get_indices(indices)

        inputs, outputs = self.run_paras(st=st, en=en, indices=indices)

        history = self.fit(inputs, outputs, **callbacks)

        plot_loss(history, name=os.path.join(self.path, "loss_curve"))

        return history

    def predict(self, st=0, en=None, indices=None):

        setattr(self, 'predict_indices', indices)

        inputs, outputs = self.run_paras(st=st, en=en, indices=indices)

        predicted = self.k_model.predict(x=inputs,
                                         batch_size=self.data_config['batch_size'],
                                         verbose=1)

        self.process_results(outputs, predicted, str(st) + '_' + str(en))

        return predicted, outputs

    def process_results(self, true, predicted, name=None):

        if np.isnan(true).sum() > 0:
            mask = np.invert(np.isnan(true.reshape(-1,)))
            true = true[mask]
            predicted = predicted[mask]

        errors = FindErrors(true, predicted)
        for er in ['mse', 'rmse', 'r2', 'nse', 'kge', 'rsr', 'percent_bias']:
            print(er, getattr(errors, er)())

        plot_results(true, predicted, name=os.path.join(self.path, name))
        return

    def build_nn(self):
        print('building simple lstm model')

        # lstm = self.nn_config['lstm_config']

        inputs = layers.Input(shape=(self.lookback, self.ins))

        lstm_activations = self.add_LSTM(inputs, self.nn_config['lstm_config'])

        predictions = layers.Dense(self.outs)(lstm_activations)

        self.k_model = self.compile(inputs, predictions)

        return

    def add_LSTM(self, inputs, config, seq=False):

        if 'name' in config:
            name = config['name']
        else:
            name = 'lstm_lyr_' + str(np.random.randint(100))

        lstm_activations = layers.LSTM(config['lstm_units'],
                               # input_shape=(self.lookback, self.ins),
                               dropout=config['dropout'],
                               recurrent_dropout=config['rec_dropout'],
                               return_sequences=seq,
                               name=name)(inputs)

        if  config['act_fn'] is not None:
            name = 'lstm_act_' + str(np.random.randint(100))
            lstm_activations = ACTIVATIONS[config['act_fn']](name=name)(lstm_activations)

        return lstm_activations

    def add_1dCNN(self, inputs, config: dict):

        if 'name' in config:
            name = config['name']
        else:
            name = 'cnn_lyr_' + str(np.random.randint(100))
        cnn_activations = layers.Conv1D(filters=config['filters'],
                                  kernel_size=config['kernel_size'],
                                  # activation=cnn['activation'],
                                  name=name)(inputs)

        if  config['act_fn'] is not None:
            name = 'cnn_act_' + str(np.random.randint(100))
            cnn_activations = ACTIVATIONS[config['act_fn']](name=name)(cnn_activations)

        max_pool_lyr = layers.MaxPooling1D(pool_size=config['max_pool_size'],
                                           name='max_pool_lyr')(cnn_activations)
        flat_lyr = layers.Flatten(name='flat_lyr')(max_pool_lyr)

        return flat_lyr

    def compile(self, model_inputs, outputs):

        k_model = self.KModel(inputs=model_inputs, outputs=outputs)
        adam = keras.optimizers.Adam(lr=self.nn_config['lr'])
        k_model.compile(loss=self.loss, optimizer=adam, metrics=['mse'])
        k_model.summary()

        return k_model

    def get_data(self, df, ins, outs):
        input_x = []
        input_y = []
        label_y = []

        row_length = len(df)
        column_length = df.columns.size
        for i in range(row_length - self.lookback+1):
            x_data = df.iloc[i:i+self.lookback, 0:column_length-outs]
            y_data = df.iloc[i:i+self.lookback-1, column_length-outs:]
            label_data = df.iloc[i+self.lookback-1, column_length-outs:]
            input_x.append(np.array(x_data))
            input_y.append(np.array(y_data))
            label_y.append(np.array(label_data))
        input_x = np.array(input_x).reshape(-1, self.lookback, ins)
        input_y = np.array(input_y).reshape(-1, self.lookback-1, outs)
        label_y = np.array(label_y).reshape(-1, outs)

        if int(df[self.data_config['outputs']].isna().sum()) > 0:
            if self.data_config['ignore_nans']:
                print("\n{} Ignoring NANs in predictions {}\n".format(10*'*', 10*'*'))
            else:
                if self.method == 'dual_attention' or self.outs > 1:
                    raise ValueError
                print('\n{} Removing Samples with nan labels  {}\n'.format(10*'*', 10*'*'))
                y = df[df.columns[-1]]
                nan_idx = y.isna()
                nan_idx_t = nan_idx[self.lookback - 1:]
                non_nan_idx = ~nan_idx_t.values
                label_y = label_y[non_nan_idx]
                input_x = input_x[non_nan_idx]
                input_y = input_y[non_nan_idx]

                assert np.isnan(label_y).sum() < 1, "label still contains nans"

        assert input_x.shape[0] == input_y.shape[0] == label_y.shape[0], "shapes are not same"

        return input_x, input_y, label_y

    def activations(self,layer_names=None, **kwargs):
        # if layer names are not specified, this will get get activations of allparameters
        inputs, outputs = self.run_paras(**kwargs)

        activations = keract.get_activations(self.k_model, inputs, layer_names=layer_names, auto_compile=True)
        return activations

    def display_activations(self, layer_name: str=None, st=0, en=None, indices=None, **kwargs):
        # not working currently because it requres the shape of activations to be (1, output_h, output_w, num_filters)
        activations = self.activations(st=st, en=en, indices=indices, layer_names=layer_name)
        if layer_name is None:
            activations = activations
        else:
            activations = activations[layer_name]

        keract.display_activations(activations=activations,**kwargs)

    def gradients_of_weights(self, st=0, en=None, indices=None) -> dict:
        test_x, test_y, test_label = self.fetch_data(self.data, st=st, en=en, shuffle=False,
                                                     cache_data=False,
                                                     indices=indices)
        return keract.get_gradients_of_trainable_weights(self.k_model, test_x, test_label)

    def gradients_of_activations(self, st=0, en=None, indices=None, layer_name=None) -> dict:
        test_x, test_y, test_label = self.fetch_data(self.data, st=st, en=en, shuffle=False,
                                                     cache_data=False,
                                                     indices=indices)

        return keract.get_gradients_of_activations(self.k_model, test_x, test_label, layer_names=layer_name)

    def trainable_weights(self):
        """ returns all trainable weights as arrays in a dictionary"""
        weights = {}
        for weight in self.k_model.trainable_weights:
           weights[weight.name] = weight.numpy()
        return weights

    def plot_weights(self, save=None):
        weights = self.trainable_weights()
        for _name, weight in weights.items():
            if np.ndim(weight) == 2 and weight.shape[1] > 1:
                self._imshow(weight, _name + " Weights", save)
            elif len(weight) > 1 and np.ndim(weight) < 3:
                plt.plot(weight)
                plt.title( _name + " Weights")
                plt.show()
            else:
                print("ignoring weight for {} because it has shape {}".format(_name, weight.shape))

    def plot_activations(self, save: bool=False, **kwargs):
        """ plots activations of intermediate layers except input and output.
        If called without any arguments then it will plot activations of all layers."""
        activations = self.activations(**kwargs)

        for lyr_name, activation in activations.items():
            if np.ndim(activation) == 2 and activation.shape[1] > 1:
                self._imshow(activation, lyr_name + " Activations", save, os.path.join(self.path, lyr_name))
            elif np.ndim(activation) == 3:
                self._imshow_3d(activation, lyr_name, save=save)
            else:
                print("ignoring activations for {} because it has shape {}".format(lyr_name, activation.shape))

    def plot_weight_grads(self, save: bool=False, **kwargs):
        """ plots gradient of all trainable weights"""

        gradients = self.gradients_of_weights(**kwargs)
        for lyr_name, gradient in gradients.items():
            if np.ndim(gradient) == 2 and gradient.shape[1] > 1:
                self._imshow(gradient, lyr_name + " Weight Gradients", save,  os.path.join(self.path, lyr_name))
            elif len(gradient) and np.ndim(gradient) < 3:
                plt.plot(gradient)
                plt.title(lyr_name + " Weight Gradients")
                plt.show()
            else:
                print("ignoring weight gradients for {} because it has shape {}".format(lyr_name, gradient.shape))

    def plot_act_grads(self, save: bool=None, **kwargs):
        """ plots activations of intermediate layers except input and output"""
        gradients = self.gradients_of_activations(**kwargs)

        for lyr_name, gradient in gradients.items():
            if np.ndim(gradient) == 2 and gradient.shape[1] > 1:
                self._imshow(gradient, lyr_name + " Activation Gradients", save, os.path.join(self.path, lyr_name))
            else:
                print("ignoring activation gradients for {} because it has shape {}".format(lyr_name, gradient.shape))

    def view_model(self, **kwargs):
        """ shows all activations, weights and gradients of the keras model."""
        self.plot_act_grads(**kwargs)
        self.plot_weight_grads(**kwargs)
        self.plot_activations(**kwargs)
        self.plot_weights()
        return

    def plot_act_along_lookback(self, activations, sample=0):

        activation = activations[sample, :, :]
        act_t = activation.transpose()

        fig, axis = plt.subplots()

        for idx, _name in enumerate(self.data_config['inputs']):
            axis.plot(act_t[idx, :], label=_name)

        axis.set_xlabel('Lookback')
        axis.set_ylabel('Input attention weight')
        axis.legend(loc="best")

        plt.show()
        return

    def plot_act_along_inputs(self, st, en, layer_name, name=None):

        pred, obs = self.predict(st, en)

        activation, data = self.activations(st=st, en=en, layer_names=layer_name)

        data = data[:, 0, :]

        assert data.shape[1] == self.ins

        plt.close('all')

        for idx in range(self.ins):

            fig, (ax1, ax2, ax3) = plt.subplots(3, sharex='all')
            fig.set_figheight(10)

            ax1.plot(data[:, idx], label=self.data_config['inputs'][idx])
            ax1.legend()
            ax1.set_title('activations w.r.t ' + self.data_config['inputs'][idx])
            ax1.set_ylabel(self.data_config['inputs'][idx])

            ax2.plot(pred, label='Prediction')
            ax2.plot(obs, label='Observed')
            ax2.legend()

            im = ax3.imshow(activation[:, :, idx].transpose(), aspect='auto')
            ax3.set_ylabel('lookback')
            ax3.set_xlabel('samples')
            fig.colorbar(im)
            plt.subplots_adjust(wspace=0.005, hspace=0.005)
            if name is not None:
                plt.savefig(name + str(idx), dpi=400, bbox_inches='tight')
            plt.show()

        return

    def plot2d_act_for_a_sample(self, activations, sample=0, name=None):
        fig, axis = plt.subplots()
        fig.set_figheight(8)
        # for idx, ax in enumerate(axis):
        im = axis.imshow(activations[sample, :, :].transpose(), aspect='auto')
        axis.set_xlabel('lookback')
        axis.set_ylabel('inputs')
        print(self.data_config['inputs'])
        axis.set_title('Activations of all inputs at different lookbacks for sample ' + str(sample))
        fig.colorbar(im)
        if name is not None:
            plt.savefig(name + '_' + str(sample), dpi=400, bbox_inches='tight')
        plt.show()
        return

    def plot1d_act_for_a_sample(self, activations, sample=0, name=None):
        fig, axis = plt.subplots()

        for idx in range(self.lookback-1):
            axis.plot(activations[sample, idx, :].transpose(), label='lookback '+str(idx))
        axis.set_xlabel('inputs')
        axis.set_ylabel('activation weight')
        axis.set_title('Activations at different lookbacks for all inputs for sample ' + str(sample))
        if name is not None:
            plt.savefig(name + '_' + str(sample), dpi=400, bbox_inches='tight')
        plt.show()

    def prepare_batches(self, data: pd.DataFrame, target: str):

        assert self.outs == 1

        x = np.zeros((len(data), self.lookback, data.shape[1] - 1))
        y = np.zeros((len(data), self.lookback, 1))

        for i, name in enumerate(list(data.columns[:-1])):
            for j in range(self.lookback):
                x[:, j, i] = data[name].shift(self.lookback - j - 1).fillna(method="bfill")

        for j in range(self.lookback):
            y[:, j, 0] = data[target].shift(self.lookback - j - 1).fillna(method="bfill")

        prediction_horizon = 1
        target = data[target].shift(-prediction_horizon).fillna(method="ffill").values

        x = x[self.lookback:]
        y = y[self.lookback:]
        target = target[self.lookback:]

        return x, y, target

    def _imshow(self, img, label, save=False, fname=None):
        plt.close('all')
        plt.imshow(img, aspect='auto')
        plt.colorbar()
        plt.title(label)
        if save:
            plt.savefig(os.path.join(self.path, fname))
        else:
            plt.show()

    def _imshow_3d(self, activation, lyr_name, save=False):
        act_2d = []
        for i in range(activation.shape[0]):
            act_2d.append(activation[i, :])
        activation_2d = np.vstack(act_2d)
        self._imshow(activation_2d, lyr_name + " Activations (3d of {})".format(activation.shape),
                     save, os.path.join(self.path, lyr_name))

    def save_config(self):

        config = dict()
        config['min_val_loss'] = np.min(
            self.k_model.history.history['val_loss']) if 'val_loss' in self.k_model.history.history else None
        config['min_loss'] = np.min(
            self.k_model.history.history['loss']) if 'val_loss' in self.k_model.history.history else None
        config['nn_config'] = self.nn_config
        config['data_config'] = self.data_config
        config['test_indices'] = np.array(self.test_indices, dtype=int) if self.test_indices is not None else None
        config['test_indices'] = np.array(self.train_indices, dtype=int) if self.train_indices is not None else None
        config['intervals'] = self.intervals
        config['method'] = self.method

        save_config_file(config=config, path=self.path)
        return config

def unison_shuffled_copies(a, b, c):
    """makes sure that all the arrays are permuted similarly"""
    assert len(a) == len(b) == len(c)
    p = np.random.permutation(len(a))
    return a[p], b[p], c[p]
