""" SGD solver class for quick training. Adapted from cs231n lab codes. """
from __future__ import division
from __future__ import absolute_import
from __future__ import print_function
import time

from minpy.nn import optim
from nn import init
from minpy import core
import numpy as np


class Solver(object):
    """
    A Solver encapsulates all the logic necessary for training classification
    models. The Solver performs stochastic gradient descent using different
    update rules defined in optim.py.

    The solver accepts both training and validataion data and labels so it can
    periodically check classification accuracy on both training and validation
    data to watch out for overfitting.

    To train a model, you will first construct a Solver instance, passing the
    model, dataset, and various optoins (learning rate, batch size, etc) to the
    constructor. You will then call the train() method to run the optimization
    procedure and train the model.

    After the train() method returns, model.params will contain the parameters
    that performed best on the validation set over the course of training.
    In addition, the instance variable solver.loss_history will contain a list
    of all losses encountered during training and the instance variables
    solver.train_acc_history and solver.val_acc_history will be lists containing
    the accuracies of the model on the training and validation set at each epoch.

    Example usage might look something like this:

    train_dataiter = NDArrayIter(data['X_train'],
                           data['y_train'],
                           batch_size=100,
                           shuffle=True)
    test_dataiter = NDArrayIter(data['X_val'],
                           data['y_val'],
                           batch_size=100,
                           shuffle=False)

    model = MyAwesomeModel(hidden_size=100, reg=10)
    solver = Solver(model, train_dataiter, test_dataiter,
                    update_rule='sgd',
                    optim_config={
                      'learning_rate': 1e-3,
                    },
                    lr_decay=0.95,
                    num_epochs=10,
                    print_every=100)
    solver.train()
    """

    def __init__(self, model, trainX, trainY, testX, testY, **kwargs):
        """
        Construct a new Solver instance.

        Required arguments:
        - model: A model object conforming to the API described above
        - train_dataiter: A data iterator for training data, we can get batch from it
          'batch.data': Array of shape (N_train, d_1, ..., d_k) giving training images
          'batch.label': Array of shape (N_val, d_1, ..., d_k) giving validation images
        - test_dataiter: A data iterator for training data, we can get batch from it

        Optional arguments:
        - update_rule: A string giving the name of an update rule in optim.py.
          Default is 'sgd'.
        - optim_config: A dictionary containing hyperparameters that will be
          passed to the chosen update rule. Each update rule requires different
          hyperparameters (see optim.py) but all update rules require a
          'learning_rate' parameter so that should always be present.
        - lr_decay: A scalar for learning rate decay; after each epoch the learning
          rate is multiplied by this value.
        - num_epochs: The number of epochs to run for during training.
        - train_acc_num_samples: The number of samples used to evaluate
          training accuracy after each iteration.
        - print_every: Integer; training losses will be printed every print_every
          iterations.
        - verbose: Boolean; if set to false then no output will be printed during
          training.
        """
        self.model = model

        # Unpack keyword arguments
        self.init_rule = kwargs.pop('init_rule', 'xavier')
        self.init_config = kwargs.pop('init_config', {})
        self.update_rule = kwargs.pop('update_rule', 'sgd')
        self.optim_config = kwargs.pop('optim_config', {})
        self.lr_decay = kwargs.pop('lr_decay', 1.0)
        self.num_epochs = kwargs.pop('num_epochs', 10)
        self.initparams = kwargs.pop('params', None)

        batch_length = kwargs.pop('batch_length', 50)
        if batch_length > trainX.shape[1]:
            batch_length = trainX.shape[1]
        self.batch_length = batch_length

        self.trainX = self._packBatches(trainX)
        self.trainY = self._packBatches(trainY)
        self.testX = self._packBatches(testX)
        self.testY = self._packBatches(testY)

        self.num_batches = self.trainX.shape[0]
        self.print_every = kwargs.pop('print_every', self.num_batches)
        self.verbose = kwargs.pop('verbose', True)

        # Throw an error if there are extra keyword arguments
        if len(kwargs) > 0:
            extra = ', '.join('"%s"' % k for k in kwargs.keys())
            raise ValueError('Unrecognized arguments %s' % extra)

        # Make sure the update rule exists, then replace the string
        # name with the actual function
        if not hasattr(optim, self.update_rule):
            raise ValueError('Invalid update_rule "%s"' % self.update_rule)
        self.update_rule = getattr(optim, self.update_rule)

        self._reset()

    def _packBatches(self, data):
        N, T, D = data.shape
        batch_num = np.max([int (T / self.batch_length),1])
        iterator = np.zeros((batch_num, N, self.batch_length, D), dtype=np.int)
        for i in xrange(int(batch_num)):
            iterator[i,:,:,:] = data[:,i*self.batch_length: (i+1)*self.batch_length,:]
        return iterator

    def _reset(self):
        """
        Set up some book-keeping variables for optimization. Don't call this
        manually.
        """
        # Set up some variables for book-keeping
        self.epoch = 0
        self.best_val_acc = 0
        self.best_params = {}
        self.loss_history = []
        self.train_acc_history = []
        self.val_acc_history = []

        # Make a deep copy of the optim_config for each parameter
        self.optim_configs = {}
        for p in self.model.param_configs:
            d = {k: v for k, v in self.optim_config.items()}
            self.optim_configs[p] = d
        # Overwrite it if the model specify the rules

        # Make a deep copy of the init_config for each parameter
        # and set each param to their own init_rule and init_config
        self.init_rules = {}
        self.init_configs = {}
        for p in self.model.param_configs:
            if 'init_rule' in self.model.param_configs[p]:
                init_rule = self.model.param_configs[p]['init_rule']
                init_config = self.model.param_configs[p].get('init_config',
                                                              {})
            else:
                init_rule = self.init_rule
                init_config = {k: v for k, v in self.init_config.items()}
            # replace string name with actual function
            if not hasattr(init, init_rule):
                raise ValueError('Invalid init_rule "%s"' % init_rule)
            init_rule = getattr(init, init_rule)
            self.init_rules[p] = init_rule
            self.init_configs[p] = init_config

    def _step(self, X_batch, y_batch):
        """
        Make a single gradient update. This is called by train() and should not
        be called manually.
        """
        # Compute loss and gradient
        def loss_func(*params):
            # It seems that params are not used in forward function. But since we will pass
            # model.params as arguments, we are ok here.
            predict = self.model.forward(X_batch, mode='train')
            return self.model.loss(predict, y_batch)

        param_arrays = list(self.model.params.values())
        param_keys = list(self.model.params.keys())
        grad_and_loss_func = core.grad_and_loss(
            loss_func, argnum=range(len(param_arrays)))
        grad_arrays, loss = grad_and_loss_func(*param_arrays)
        grads = dict(zip(param_keys, grad_arrays))

        self.loss_history.append(loss.asnumpy())

        # Perform a parameter update
        for p, w in self.model.params.items():
            dw = grads[p]
            config = self.optim_configs[p]
            next_w, next_config = self.update_rule(w, dw, config)
            self.model.params[p] = next_w
            self.optim_configs[p] = next_config


    def check_accuracy(self, X, y):
        """
        Check accuracy of the model on the provided data.

        Inputs:
        - dataiter: data iterator that can produce batches.
        - num_samples: If not None and dataiter has more than num_samples datapoints,
          subsample the data and only test the model on num_samples datapoints.

        Returns:
        - acc: Scalar giving the fraction of instances that were correctly
          classified by the model.
        """

        acc_count = 0
        num_samples = 0
        for i in xrange(X.shape[0]):
            X_batch = X[i]
            y_batch = y[i]
            predict = self.model.forward(
                X_batch, mode='test').asnumpy()
            # Customized!
            translator = np.array([0,1,2],dtype=np.int)
            compressed_label = np.sum(y_batch * translator, axis=2)
            acc_count += np.sum(
                np.argmax(
                    predict, axis=2) == compressed_label)
            num_samples += compressed_label.size
        return float(acc_count) / num_samples

    def init(self):
        """
        Init model parameters based on the param_configs in model
        """
        
        if self.initparams is not None:
            for name in self.initparams.keys():
                self.model.params[name] = self.initparams[name]
        else:
            for name, config in self.model.param_configs.items():
                self.model.params[name] = self.init_rules[name](
                    config['shape'], self.init_configs[name])
            for name, value in self.model.aux_param_configs.items():
                self.model.aux_params[name] = value

    def change_settings(self,learning_rate = None, num_epochs = None):
        if learning_rate is not None:
            self.optim_config = {'learning_rate': learning_rate}
        if num_epochs is not None:
            self.num_epochs = num_epochs

    def train(self):
        """
        Run optimization to train the model.
        """

        num_iterations = self.num_batches * self.num_epochs
        t = 0
        
        for epoch in range(self.num_epochs):
            start = time.time()
            self.epoch = epoch + 1
            time_spent_steps = 0
            self.model.reset_history()
            for i in xrange(self.num_batches):
                self._step(self.trainX[i,:,:,:], self.trainY[i,:,:,:])
                # Maybe print training loss
                if self.verbose and t % self.print_every == 0:
                    print('(Iteration %d / %d) loss: %f' %
                          (t + 1, num_iterations, self.loss_history[-1]))
                t += 1

            # evaluate after each epoch
            train_acc = self.check_accuracy(self.trainX, self.trainY)
            val_acc = self.check_accuracy(self.testX, self.testY)
            self.train_acc_history.append(train_acc)
            self.val_acc_history.append(val_acc)

            if self.verbose:
                print('(Epoch {} / {}) train acc: {}, val_acc: {}, time: {}.'.
                      format(self.epoch, self.num_epochs, train_acc, val_acc,
                             time.time() - start))

            # Keep track of the best model
            if val_acc > self.best_val_acc:
                self.best_val_acc = val_acc
                self.best_params = {}
                for k, v in self.model.params.items():
                    #TODO: Missing Copy Method
                    #self.best_params[k] = v.copy()
                    self.best_params[k] = v

            for k in self.optim_configs:
                self.optim_configs[k]['learning_rate'] *= self.lr_decay

        # At the end of training swap the best params into the model
        self.model.params = self.best_params
