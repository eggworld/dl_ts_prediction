__all__ = ["HARHNModel", "IMVLSTMModel"]

import numpy as np
from sklearn.model_selection import train_test_split
import matplotlib.pyplot as plt
import os

from .global_variables import torch
from .HARHN import HARHN
from .imv_networks import IMVTensorLSTM
from main import Model


class HARHNModel(Model):

    def __init__(self, **kwargs):
        self.api = 'pytorch'
        self.pt_model = None
        self.opt = None
        self.epoch_scheduler = None
        self.min_max = None
        self.saved_model = None

        super(HARHNModel, self).__init__(**kwargs)

    @property
    def use_predicted_output(self):
        return self.data_config['use_predicted_output']

    def build_nn(self):
        config = self.nn_config['HARHN_config']

        self.pt_model = HARHN(config['n_conv_lyrs'],
                              self.lookback, self.ins, self.outs,
                              n_units_enc=config['enc_units'],
                              n_units_dec=config['dec_units'],
                              use_predicted_output=self.data_config['use_predicted_output']).cuda()
        self.opt = torch.optim.Adam(self.pt_model.parameters(), lr=self.nn_config['lr'])

        self.epoch_scheduler = torch.optim.lr_scheduler.StepLR(self.opt, 20, gamma=0.9)

        self.loss = torch.nn.MSELoss()

        return

    def train_epoch_v2(self, data_loader):
        # using previous predictions as input
        batch_y_h = torch.zeros(self.data_config['batch_size'], 1)
        mse_train = 0
        for batch_x, _, batch_y in data_loader:
            batch_x = batch_x.cuda()
            batch_y = batch_y.cuda()
            batch_y_h = batch_y_h.cuda()
            self.opt.zero_grad()
            y_pred, batch_y_h = self.pt_model(batch_x, batch_y_h)
            batch_y_h = batch_y_h.detach()
            y_pred = y_pred.squeeze(1)
            l = self.loss(y_pred, batch_y)
            l.backward()
            mse_train += l.item() * batch_x.shape[0]
            self.opt.step()

        return mse_train

    def train_epoch_v1(self, data_loader):
        # using previous observations as input
        mse_train = 0
        for batch_x, batch_y_h, batch_y in data_loader:
            batch_x = batch_x.cuda()
            batch_y = batch_y.cuda()
            batch_y_h = batch_y_h.cuda()
            self.opt.zero_grad()
            y_pred, _ = self.pt_model(batch_x, batch_y_h)
            y_pred = y_pred.squeeze(1)
            l = self.loss(y_pred, batch_y)
            l.backward()
            mse_train += l.item() * batch_x.shape[0]
            self.opt.step()

        return mse_train

    def eval_epoch_v1(self, data_loader):
        mse_val = 0
        preds = []
        true = []
        for batch_x, batch_y_h1, batch_y in data_loader:
            batch_x = batch_x.cuda()
            batch_y = batch_y.cuda()
            batch_y_h1 = batch_y_h1.cuda()
            output, _ = self.pt_model(batch_x, batch_y_h1)
            output = output.squeeze(1)
            preds.append(output.detach().cpu().numpy())
            true.append(batch_y.detach().cpu().numpy())
            mse_val += self.loss(output, batch_y).item() * batch_x.shape[0]

        return true, preds, mse_val

    def eval_epoch_v2(self, data_loader):
        mse_val = 0
        preds = []
        true = []
        batch_y_h1 = torch.zeros(16, 1)
        for batch_x, _, batch_y in data_loader:
            batch_x = batch_x.cuda()
            batch_y = batch_y.cuda()
            batch_y_h1 = batch_y_h1.cuda()
            output, batch_y_h1 = self.pt_model(batch_x, batch_y_h1)
            batch_y_h1 = batch_y_h1.detach()
            output = output.squeeze(1)
            preds.append(output.detach().cpu().numpy())
            true.append(batch_y.detach().cpu().numpy())
            mse_val += self.loss(output, batch_y).item() * batch_x.shape[0]

        return true, preds, mse_val

    def train_nn(self, st=0, en=None, indices=None, **callbacks):

        x, y_his, target = self.prepare_batches(self.data[st:en],  self.data_config['outputs'][0])

        x_tr, x_val, y_his_tr, y_his_val, target_tr, target_val = train_test_split(x, y_his, target,
                                                                                   test_size=self.data_config['val_fraction'])

        self.min_max = {
            'x_max': x_tr.max(axis=0),
            'x_min': x_tr.min(axis=0),
            'y_his_max': y_his_tr.max(axis=0),
            'y_his_min': y_his_tr.min(axis=0),
            'target_max': target_tr.max(axis=0),
            'target_min': target_tr.min(axis=0)
        }

        x_train = (x_tr - self.min_max['x_min']) / (self.min_max['x_max'] - self.min_max['x_min'])
        x_val = (x_val - self.min_max['x_min']) / (self.min_max['x_max'] - self.min_max['x_min'])

        y_his_train = (y_his_tr - self.min_max['y_his_min']) / (self.min_max['y_his_max'] - self.min_max['y_his_min'])
        y_his_val = (y_his_val - self.min_max['y_his_min']) / (self.min_max['y_his_max'] - self.min_max['y_his_min'])

        target_train = (target_tr - self.min_max['target_min']) / (self.min_max['target_max'] - self.min_max['target_min'])
        target_val = (target_val - self.min_max['target_min']) / (self.min_max['target_max'] - self.min_max['target_min'])

        x_train_t = to_torch_tensor(x_train)
        x_val_t = to_torch_tensor(x_val)

        y_his_train_t = to_torch_tensor(y_his_train)
        y_his_val_t = to_torch_tensor(y_his_val)

        target_train_t = to_torch_tensor(target_train)
        target_val_t = to_torch_tensor(target_val)

        data_train_loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(x_train_t, y_his_train_t, target_train_t), shuffle=True,
                                       batch_size=self.data_config['batch_size'])
        data_val_loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(x_val_t, y_his_val_t, target_val_t), shuffle=False,
                                     batch_size=self.data_config['batch_size'])

        min_val_loss = self.nn_config['min_val_loss']
        counter = 0
        losses = {'train_loss': [],
                  'val_loss': []}

        for i in range(self.nn_config['epochs']):
            if self.use_predicted_output:
                mse_train = self.train_epoch_v2(data_train_loader)
            else:
                mse_train = self.train_epoch_v1(data_train_loader)

            self.epoch_scheduler.step()

            with torch.no_grad():
                if self.use_predicted_output:
                    true, preds, mse_val = self.eval_epoch_v2(data_val_loader)
                else:
                    true, preds, mse_val = self.eval_epoch_v1(data_val_loader)
            preds = np.concatenate(preds)
            true = np.concatenate(true)

            if min_val_loss > mse_val ** 0.5:
                min_val_loss = mse_val ** 0.5
                print("Saving...")
                self.saved_model = os.path.join(self.path, "harhn_nasdaq.pt")
                torch.save(self.pt_model.state_dict(), self.saved_model)
                counter = 0
            else:
                counter += 1

            if counter == self.nn_config['patience']:
                print("Training is stopped because patience reached")
                break
            train_loss = (mse_train / len(x_train_t)) ** 0.5
            val_loss = (mse_val / len(x_val_t)) ** 0.5
            losses['train_loss'].append(train_loss)
            losses['val_loss'].append(val_loss)
            print("Iter: ", i, "train: ", train_loss, "val: ", val_loss)
            if i % 10 == 0:
                preds = preds * (self.min_max['target_max'] - self.min_max['target_min']) + self.min_max['target_min']
                true = true * (self.min_max['target_max'] - self.min_max['target_min']) + self.min_max['target_min']

                self.process_results(true, preds, 'validation_' + str(i))

        return losses

    def predict(self, st=0, ende=None, indices=None):
        x_test, y_his_test, target_test = self.prepare_batches(self.data[st:ende],  self.data_config['outputs'][0])
        x_test = (x_test - self.min_max['x_min']) / (self.min_max['x_max'] - self.min_max['x_min'])
        y_his_test = (y_his_test - self.min_max['y_his_min']) / (self.min_max['y_his_max'] - self.min_max['y_his_min'])
        target_test = (target_test - self.min_max['target_min']) / (self.min_max['target_max'] - self.min_max['target_min'])

        x_test_t = to_torch_tensor(x_test)
        y_his_test_t = to_torch_tensor(y_his_test)
        target_test_t = to_torch_tensor(target_test)

        data_test_loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(x_test_t, y_his_test_t, target_test_t), shuffle=False,
                                      batch_size=self.data_config['batch_size'])

        self.pt_model.load_state_dict(torch.load(self.saved_model))

        with torch.no_grad():
            if self.use_predicted_output:
                true, preds, mse_val = self.eval_epoch_v2(data_test_loader)
            else:
                true, preds, mse_val = self.eval_epoch_v1(data_test_loader)
        preds = np.concatenate(preds)
        true = np.concatenate(true)

        preds = preds*(self.min_max['target_max'] - self.min_max['target_min']) + self.min_max['target_min']
        true = true*(self.min_max['target_max'] - self.min_max['target_min']) + self.min_max['target_min']

        self.process_results(true, preds, 'validation_')

        return

class IMVLSTMModel(HARHNModel):

    def __init__(self, **kwargs):
        self.alphas = None
        self.betas = None
        super(IMVLSTMModel, self).__init__(**kwargs)

    def build_nn(self):

        if self.use_predicted_output:
            ins = self.ins
        else:
            ins = self.ins + self.outs

        self.pt_model = IMVTensorLSTM(ins, self.outs, self.data_config['batch_size']).cuda()

        self.opt = torch.optim.Adam(self.pt_model.parameters(), lr=self.nn_config['lr'])

        self.epoch_scheduler = torch.optim.lr_scheduler.StepLR(self.opt, 20, gamma=0.9)

        self.loss = torch.nn.MSELoss()

        return

    def train_nn(self, st=0, en=None, indices=None, **callbacks):

        x, y_h, target = self.prepare_batches(self.data[st:en], self.data_config['outputs'][0])

        if not self.use_predicted_output:
            x = np.dstack([x, y_h])
        x_train, x_val, target_train, target_val = train_test_split(x, target, test_size=self.data_config['val_fraction'])

        self.min_max = {
            'x_max': x_train.max(axis=0),
            'x_min': x_train.min(axis=0),
            'target_max': target_train.max(axis=0),
            'target_min': target_train.min(axis=0)
        }

        x_train = (x_train - self.min_max['x_min']) / (self.min_max['x_max'] - self.min_max['x_min'])
        target_train = (target_train - self.min_max['target_min']) / (self.min_max['target_max'] - self.min_max['target_min'])
        x_val = (x_val - self.min_max['x_min']) / (self.min_max['x_max'] - self.min_max['x_min'])
        target_val = (target_val - self.min_max['target_min']) / (self.min_max['target_max'] - self.min_max['target_min'])

        x_train_t = to_torch_tensor(x_train)
        target_train_t = to_torch_tensor(target_train)
        x_val_t = to_torch_tensor(x_val)
        target_val_t = to_torch_tensor(target_val)

        data_train_loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(x_train_t, target_train_t),
                                       shuffle=True, batch_size=self.data_config['batch_size'])
        data_val_loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(x_val_t, target_val_t),
                                     shuffle=False, batch_size=self.data_config['batch_size'])

        min_val_loss = self.nn_config['min_val_loss']
        counter = 0
        losses = {'train_loss': [],
                  'val_loss': []}
        for i in range(self.nn_config['epochs']):
            mse_train = 0
            for batch_x, batch_y in data_train_loader:
                batch_x = batch_x.cuda()
                batch_y = batch_y.cuda()
                self.opt.zero_grad()
                y_pred, alphas, betas = self.pt_model(batch_x)
                y_pred = y_pred.squeeze(1)
                l = self.loss(y_pred, batch_y)
                l.backward()
                mse_train += l.item() * batch_x.shape[0]
                self.opt.step()
            self.epoch_scheduler.step()
            with torch.no_grad():
                mse_val = 0
                preds = []
                true = []
                for batch_x, batch_y in data_val_loader:
                    batch_x = batch_x.cuda()
                    batch_y = batch_y.cuda()
                    output, alphas, betas = self.pt_model(batch_x)
                    output = output.squeeze(1)
                    preds.append(output.detach().cpu().numpy())
                    true.append(batch_y.detach().cpu().numpy())
                    mse_val += self.loss(output, batch_y).item() * batch_x.shape[0]
            pred = np.concatenate(preds)
            true = np.concatenate(true)

            if min_val_loss > mse_val ** 0.5:
                min_val_loss = mse_val ** 0.5
                print("Saving...")
                self.saved_model = os.path.join(self.path, "imv_tensor_lstm_nasdaq.pt")
                torch.save(self.pt_model.state_dict(), self.saved_model)
                counter = 0
            else:
                counter += 1

            if counter == self.nn_config['patience']:
                break
            train_loss = (mse_train / len(x_train_t)) ** 0.5
            val_loss = (mse_val / len(x_val_t)) ** 0.5
            losses['train_loss'].append(train_loss)
            losses['val_loss'].append(val_loss)
            print("Iter: ", i, "train: ", train_loss, "val: ", val_loss)
            if i % 10 == 0:
                pred = pred * (self.min_max['target_max'] - self.min_max['target_min']) + self.min_max['target_min']
                true = true * (self.min_max['target_max'] - self.min_max['target_min']) + self.min_max['target_min']

                self.process_results(true, pred, str(i))

        return

    def predict(self, st=0, ende=None, indices=None):
        self.pt_model.load_state_dict(torch.load(self.saved_model))

        x_test, y_h, target_test = self.prepare_batches(self.data[st:ende], self.data_config['outputs'][0])

        if not self.use_predicted_output:
            x_test = np.dstack([x_test, y_h])

        x_test = (x_test - self.min_max['x_min']) / (self.min_max['x_max'] - self.min_max['x_min'])
        target_test = (target_test - self.min_max['target_min']) / (self.min_max['target_max'] - self.min_max['target_min'])

        x_test_t = to_torch_tensor(x_test)
        target_test_t = to_torch_tensor(target_test)

        data_test_loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(x_test_t, target_test_t),
                                      shuffle=False, batch_size=self.data_config['batch_size'])

        with torch.no_grad():
            mse_val = 0
            preds = []
            true = []
            self.alphas = []
            self.betas = []
            for batch_x, batch_y in data_test_loader:
                batch_x = batch_x.cuda()
                batch_y = batch_y.cuda()
                output, a, b = self.pt_model(batch_x)
                output = output.squeeze(1)
                preds.append(output.detach().cpu().numpy())
                true.append(batch_y.detach().cpu().numpy())
                self.alphas.append(a.detach().cpu().numpy())
                self.betas.append(b.detach().cpu().numpy())
                mse_val += self.loss(output, batch_y).item()*batch_x.shape[0]
        preds = np.concatenate(preds)
        true = np.concatenate(true)

        preds = preds*(self.min_max['target_max'] - self.min_max['target_min']) + self.min_max['target_min']
        true = true*(self.min_max['target_max'] - self.min_max['target_min']) + self.min_max['target_min']

        self.process_results(true, preds, 'validation')

        return preds, true

    def plot_activations(self):
        alphas = np.concatenate(self.alphas)
        betas = np.concatenate(self.betas)

        alphas = alphas.mean(axis=0)
        betas = betas.mean(axis=0)

        alphas = alphas[..., 0]
        betas = betas[..., 0]

        alphas = alphas.transpose(1, 0)

        all_cols = self.data_config['inputs'] + self.data_config['outputs']
        plt.close('all')
        fig, ax = plt.subplots()
        # fig.set_figwidth(12)
        # fig.set_figheight(14)
        plt.figure(dpi=400)
        _ = ax.imshow(alphas)
        ax.set_xticks(np.arange(self.lookback))
        ax.set_yticks(np.arange(len(all_cols)))
        ax.set_xticklabels(["t-"+str(i) for i in np.arange(self.lookback, -1, -1)])
        ax.set_yticklabels(list(all_cols))
        for i in range(len(all_cols)):
            for j in range(self.lookback):
                _ = ax.text(j, i, round(alphas[i, j], 3),
                               ha="center", va="center", color="w")
        ax.set_title("Importance of features and timesteps")

        plt.show()

        plt.figure()
        plt.title("Feature importance")
        plt.bar(range(self.ins + self.outs), betas)
        plt.xticks(ticks=range(len(all_cols)), labels=list(all_cols), rotation=90)

def to_torch_tensor(array):
    return torch.Tensor(array)
