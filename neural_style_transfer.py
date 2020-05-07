# -*- coding: utf-8 -*-
"""neural_style_transfer.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1Q7-5-KrI2j-m6TH9pwNUZsBjkVHPlgz6
"""

import numpy as np
import matplotlib.pyplot as plt
import skimage as sk
import skimage.transform
import skimage.io as skio

import torch
from torchvision import models, transforms, utils
import torch.nn as nn
from torch.autograd import Variable
from torch.optim import LBFGS, SGD

class StyleTransferNet(torch.nn.Module):
    
    def __init__(self):
        """
        Use pre-trained VGG-19 network
        """
        super(StyleTransferNet, self).__init__()
        self.vgg = models.vgg19(pretrained=True)

    def forward(self, x):
        """
        Since we compute the loss from the outputs of each layer, 
        we process the input 1 layer at a time and return them
        """
        
        # Only keep track of convolutional layer outputs since only those
        # are used in the loss calculations
        conv_outputs = []
        
        for layer in self.vgg.features:

            # Suggested in Gatys to convert MaxPool to AvgPool
            if type(layer) == nn.modules.pooling.MaxPool2d:
                layer = nn.AvgPool2d(kernel_size=2, stride=2, padding=0, ceil_mode=False)

            x = layer(x)

            if type(layer) == nn.modules.conv.Conv2d:
                conv_outputs.append(x)
            
        return conv_outputs

def preprocess(im, shape):
    
    im = sk.transform.resize(im, shape)

    mean_vec = np.mean(im, axis=(0,1))
    std_vec = np.std(im, axis=(0,1))
    
    # Normalize all inputs before passing into model
    transform = transforms.Compose([transforms.ToTensor()])
                                # transforms.Normalize(mean=mean_vec, std=std_vec)])
    
    # Move to range [-1,1]
    im = sk.img_as_float(im)
    im = transform(im)
    
    # Model wants 4d input
    im = im.unsqueeze(0)

    return im.type(tensor_type)

def content_loss(content_layers, generated_out, content_out):
    
    """ 
    The Gatys paper defines a matrix for content loss, called P and F 
    for the content input p and generated image x appropriately. They are defined
    wrt each layer. They are organized as follows:
        
    [[Output of 1st filter in layer],
     [Output of 2nd filter in layer],
     ...]

    These are already given to us in the layer outputs of the parameters.
    
    The content loss is the Sum of Squared Distances between P and F. This is
    mathematically equivalent to taking the Frobenius norm of P - F, without needing
    to flatten anything. This is becuase the Frobenius norm is row blind, i.e. 
    regardless if we flatten anything, it will just take the overall sum
    of all the squared entries.
    
    Note: Turns out stacking tensors into a list then converting that into a tensor
    is a pain. So, instead, I just utilized the fact the we are only using one layer
    in practice and just stick with that layer.
    """

    # desired_layers = [layers[l] for l in content_layers]
    # errors = []
    
    # for l in desired_layers:
        
    #     content_layer, generated_layer = content_out[l], generated_out[l]
    #     diff = torch.norm(content_layer - generated_layer)**2
    #     errors.append(0.5 * diff)

    # error = sum(errors)
    
    # # Using python sum over torch.sum fixed autodiff things for some reason
    # return error

    wanted_layers = [layers[l] for l in content_layers]
    differences = []
    for i in wanted_layers:
        content, target = content_out[i], generated_out[i]
        differences.append(torch.mean((content - target)**2))
    return sum(differences)

def style_loss(style_layers, generated_out, style_out, layer_weights):
    
    """
    The paper defines G (the Gram matrix), wrt to each layer, as a matrix whose ij'th entry contains the
    inner product of the i'th and j'th filter outputs:
    
    [[Inner product of 1st filter output with itself, Inner product of 1st filter output with 2nd filter output, ...],
     [Inner product of 2st filter output with 1st filter output, Inner product of 2nd filter output with itself, ...],
     ...]
     
    Similar to content loss, we define two matrices, G and A, for the generated and style image respectively.
    Then, the style loss it Mean Squared Error between G and A. This time, we can't leverage the same
    structure as before, so we first need to create a matrix where each row is the flattened filter output,
    then take the inner product of this and its transpose. (If you wanted to use the Frobenius norm, you would
    need to compute it individually for ever combination of filters, which is tedious).
    
    This is actually just the same as finding the covariance matrix where the features are the filters outputs.
    
    Next, we need to normalize by a factor of 1 / (number of filters in layer)^2 * (size of filter output)^2
    
    Finally, since this is computed across layers, we take a weighted average of these quantities. In the
    paper, they suggest using a uniform distribution of weights.
    """
    
    # Shape of layer outputs are (1, num_filters, y, x)
    
    # desired_layers = [layers[l] for l in style_layers]
    # errors = []
    
    # for l in desired_layers:
        
    #     style_layer, generated_layer = style_out[l], generated_out[l]
    #     _, N, y, x = style_layer.shape
    #     M = y * x
        
    #     style_feature_matrix = style_layer.squeeze().view(N, M)
    #     generated_feature_matrix = generated_layer.squeeze().view(N, M)
        
    #     A = style_feature_matrix @ style_feature_matrix.T
    #     G = generated_feature_matrix @ generated_feature_matrix.T
        
    #     # mse = torch.norm(A - G)**2 / (4 * N**2 * M**2)
    #     # Taking the mean automatically adds everything up and divides it by N * M
    #     mse = torch.mean((A - G)**2) / (4 * N * M)
    #     errors.append(mse)

    # error = sum([layer_weights[i] * errors[i] for i in torch.arange(len(errors))])
        
    # return error

    wanted_layers = [layers[l] for l in style_layers]
    layer_expectations = []
    for l in wanted_layers:
        style_layer = style_out[l]
        target_layer = generated_out[l]
        _, N, y, x = style_layer.data.size()
        M = y * x
        style_layer = style_layer.view(N, M)
        target_layer = target_layer.view(N, M)
        # compute the Gram matrices - the auto-correlation of each filter activation
        G_s = torch.mm(style_layer, style_layer.t())
        G_t = torch.mm(target_layer, target_layer.t())
        # MSE of differences between the Gram matrices
        difference = torch.mean(((G_s - G_t) ** 2)/(M*N*2))
        normalized_difference = 0.2*(difference)
        layer_expectations.append(normalized_difference)
    return sum(layer_expectations)

def closure():

  optimizer.zero_grad()
  generated_out = net(generated)
    
  # For LBFGS, closure cannot take any arguments, so make this a HOF wrapped by another func
  # with generated_im, content_out, and style_out defined there

  closs = content_loss(content_layers, generated_out, content_out)
  sloss = style_loss(style_layers, generated_out, style_out, layer_weights)

  loss = content_weight * closs + style_weight + sloss

  print("Step %s: %s" % (i, loss.cpu().item()))

  # print(closs, sloss, loss)

  closs.backward(retain_graph=True)
  sloss.backward(retain_graph=True)

  return loss

def save_im(generated_im):

    result = generated_im.detach().cpu().squeeze(0).data
    # np_im = print_im.numpy().T

    # mean_vec = np.mean(np_im, axis=(0,1))
    # std_vec = np.std(np_im, axis=(0,1))
    # transform = transforms.Normalize(mean=mean_vec, std=std_vec)
    # result = transform(print_im)

    # print_im = result.numpy().T
    # result = (print_im - np.min(print_im))/np.ptp(print_im)

    utils.save_image(result, "result.jpg")

layer_names = ["conv1_1", "conv1_2", 
               "conv2_1", "conv2_2", 
               "conv3_1", "conv3_2", "conv3_3", "conv3_4", 
               "conv4_1", "conv4_2", "conv4_3", "conv4_4", 
               "conv5_1", "conv5_2", "conv5_3", "conv5_4"]

# This will allow us to index into the convolutional layer outputs of the network by name, as done in the paper
layers = {layer_names[i]: i for i in torch.arange(len(layer_names))}

content_layers = ["conv4_2"]
style_layers = ["conv1_1", "conv2_1", "conv3_1", "conv4_1", "conv5_1"]

layer_weights = [0.2, 0.2, 0.2, 0.2, 0.2]

# Gatys suggests ratio of 1000:1 or 10000:1
content_weight = 1
style_weight = 10000

def main():

    if torch.cuda.is_available():
      device = torch.device("cuda")
      tensor_type = torch.cuda.FloatTensor
    else:
      device = torch.device("cpu")
      tensor_type = torch.FloatTensor

    content_im = skio.imread("neckarfront.jpg")
    style_im = skio.imread("starry_night.jpg")

    # Preprocessing of inputs
    shape = content_im.shape

    content_im = preprocess(content_im, shape)
    style_im = preprocess(style_im, shape)

    generated_im = torch.randn([1, 3, shape[0], shape[1]]).type(tensor_type).requires_grad_(True)
    # generated_im = content_im.clone().requires_grad_(True)

    generated_im.to(device)
    content_im.to(device)
    style_im.to(device)

    net = StyleTransferNet()
    net.to(device)
    optimizer = LBFGS([generated_im])

    content_out = net(content_im)
    style_out = net(style_im)

    for i in range(300):
        optimizer.step(closure)

    save_im(generated_im)

