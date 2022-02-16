import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from torchvision.transforms.functional import pad
import time,sys
from util.tools import *

class ConvBlock(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, stride, padding, name, batchnorm, act='leaky'):
        super().__init__()
        self.conv = nn.Conv2d(in_channels, out_channels, kernel_size=kernel_size, stride=stride, padding=padding)
        self.bn = nn.BatchNorm2d(out_channels)
        if act == 'leaky':
            self.act = nn.LeakyReLU()
        elif act == 'relu':
            self.act = nn.ReLU()
        
        self.module = nn.Sequential()
        self.module.add_module(name+'_conv', self.conv)
        if batchnorm == 1:
            self.module.add_module(name+"_bn", self.bn)
        if act != 'linear':
            self.module.add_module(name+"_act", self.act)

    def forward(self, x):
        return self.module(x)

class ResBlock(nn.Module):
    def __init__(self, in_channels, mid_channels, kernel_size = 3):
        super().__init__()
        self.conv_pointwise = nn.Conv2d(in_channels, mid_channels, kernel_size = 1)
        self.bn_pt = nn.BatchNorm2d(mid_channels)
        self.act = nn.LeakyReLU()
        self.conv = nn.Conv2d(mid_channels, in_channels, kernel_size = kernel_size, padding=1, stride=1)
        self.bn_conv = nn.BatchNorm2d(in_channels)
        self.module = nn.Sequential(self.conv_pointwise,
                                   self.bn_pt,
                                   self.act,
                                   self.conv,
                                   self.bn_conv,
                                   self.act)
    
    def forward(self, x):
        return x + self.module(x)

class ConvUp(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, stride, padding, up_ratio):
        super().__init__()
        self.conv = nn.Conv2d(in_channels, out_channels, kernel_size=kernel_size, stride=stride, padding=padding)
        self.bn = nn.BatchNorm2d(out_channels)
        self.act = nn.LeakyReLU()        
        self.upsample = nn.Upsample(scale_factor=up_ratio, mode='nearest')
        self.module = nn.Sequential(self.conv,
                                   self.bn,
                                   self.act,
                                   self.upsample)
    def forward(self, x):
        return self.module(x)

# class Upsample(nn.Module):
#     def __init__(self, size, mode="nearest"):
#         self.size = size
#         self.mode = mode
#     def forward(self, x):
#         x = F.interpolate(x, scale_factor = self.size, mode = self.mode)
#         return x

class YoloLayer(nn.Module):
    def __init__(self, layer_idx, layer_info, in_channel, in_width, in_height):
        super().__init__()
        self.n_classes = int(layer_info['classes'])
        self.ignore_thresh = float(layer_info['ignore_thresh'])
        self.box_attr = self.n_classes + 5
        mask_idxes = [int(x) for x in layer_info["mask"].split(",")]
        anchor_all = [int(x) for x in layer_info["anchors"].split(",")]
        anchor_all = [(anchor_all[i],anchor_all[i+1]) for i in range(0,len(anchor_all),2)]
        self.anchor = [anchor_all[x] for x in mask_idxes]
        self.in_width = in_width
        self.in_height = in_height
        
    def forward(self, x):           
        return x
        

def make_conv_layer(layer_idx, layer_info, in_channel):
    filters = int(layer_info['filters'])
    size = int(layer_info['size'])
    stride = int(layer_info['stride'])
    pad = int(layer_info['pad'])
    modules = nn.Sequential()
    modules.add_module('layer_'+str(layer_idx)+'_conv',
                      nn.Conv2d(in_channel,
                                filters,
                                size,
                                stride,
                                pad))

    if layer_info['batch_normalize'] == '1':
        modules.add_module('layer_'+str(layer_idx)+'_bn',
                          nn.BatchNorm2d(filters))
    if layer_info['activation'] == 'leaky':
        modules.add_module('layer_'+str(layer_idx)+'_act',
                          nn.LeakyReLU())
    elif layer_info['activation'] == 'relu':
        modules.add_module('layer_'+str(layer_idx)+'_act',
                          nn.ReLU())
    return modules

def make_shortcut_layer(modules, layer_idx):
    modules.add_module('layer_'+str(layer_idx)+'_shortcut', nn.Sequential())

class DarkNet53(nn.Module):
    def __init__(self, cfg, is_train):
        super().__init__()
        self.is_train = is_train
        self.batch = None
        self.n_channels = None
        self.in_width = None
        self.in_height = None
        self.n_classes = None
        self.module_cfg = parse_model_config(cfg)
        self.module_list = self.set_layer(self.module_cfg)
        self.yolo_layers = [layer for layer in self.module_list if isinstance(layer, YoloLayer)]
        self.box_per_anchor = 3
        # self.anchor_size = [1.19, 1.99,     # width, height for anchor 1
        #            2.79, 4.60,     # width, height for anchor 2
        #            4.54, 8.93,     # etc.
        #            8.06, 5.29,
        #            10.33, 10.65]
        self.fpn_grid_size = [self.in_width // 32, self.in_height // 32, self.in_width // 16, self.in_height // 16, self.in_width // 8, self.in_height // 8]
        self.stride = [self.get_grid_wh(j) for j in range(3)]
        
        self.initialize_weights()
        
        self.softmax = nn.Softmax(dim=1)
        
        # self.conv1 = ConvBlock(in_channels = 3, out_channels = 32, kernel_size=3, stride = 1, padding = 1, name="layer1", act='leakyrelu')

        # self.conv2 = ConvBlock(in_channels = 32, out_channels = 64, kernel_size=3, stride = 2, padding = 1, name="layer2", act='leakyrelu')

        # self.resblock1 = ResBlock(64, 32)

        # self.conv3 = nn.Conv2d(64, 128, kernel_size=3, stride=2, padding=1)

        # self.resblock2 = ResBlock(128, 64)

        # self.conv4 = nn.Conv2d(128, 256, kernel_size=3, stride=2, padding=1)

        # self.resblock3 = ResBlock(256, 128)

        # self.conv5 = nn.Conv2d(256, 512, kernel_size=3, stride=2, padding=1)

        # self.resblock4 = ResBlock(512, 256)

        # self.conv6 = nn.Conv2d(512, 1024, kernel_size=3, stride=2, padding=1)

        # self.resblock5 = ResBlock(1024, 512)
        
        # self.conv7 = nn.Sequential(ConvBlock(in_channels = 1024, out_channels = 512, kernel_size=1, stride=1, padding=0, name="layer12_0", act='leakyrelu'),
        #                            ConvBlock(in_channels = 512, out_channels = 1024, kernel_size=3, stride=1, padding=1, name="layer12_1", act='leakyrelu'),
        #                            ConvBlock(in_channels = 1024, out_channels = 512, kernel_size=1, stride=1, padding=0, name="layer12_2", act='leakyrelu'),
        #                            ConvBlock(in_channels = 512, out_channels = 1024, kernel_size=3, stride=1, padding=1, name="layer12_3", act='leakyrelu'),
        #                            ConvBlock(in_channels = 1024, out_channels = 512, kernel_size=1, stride=1, padding=0, name="layer12_4", act='leakyrelu'))
        # #FPN1
        # self.fpn1_a = ConvUp(in_channels=256, out_channels=128, kernel_size=1, stride=1, padding=0, up_ratio=2)
        # self.fpn1_b = nn.Sequential(ConvBlock(in_channels = 384, out_channels = 128, kernel_size=1, stride=1, padding=0, name="fpn1_b_0", act='leakyrelu'),
        #                             ConvBlock(in_channels = 128, out_channels = 256, kernel_size=3, stride=1, padding=1, name="fpn1_b_1", act='leakyrelu'),
        #                             ConvBlock(in_channels = 256, out_channels = 128, kernel_size=1, stride=1, padding=0, name="fpn1_b_2", act='leakyrelu'),
        #                             ConvBlock(in_channels = 128, out_channels = 256, kernel_size=3, stride=1, padding=1, name="fpn1_b_3", act='leakyrelu'),
        #                             ConvBlock(in_channels = 256, out_channels = 128, kernel_size=1, stride=1, padding=0, name="fpn1_b_4", act='leakyrelu'),
        #                             ConvBlock(in_channels = 128, out_channels = 256, kernel_size=3, stride=1, padding=1, name="fpn1_b_5", act='leakyrelu'))
        # self.fpn1_c = ConvBlock(in_channels=256, out_channels=self.output_channels, kernel_size=1, stride=1, padding=0, name="fpn1_c", act='linear')
        # #FPN2
        # self.fpn2_a = ConvUp(in_channels=512, out_channels=256, kernel_size=1, stride=1, padding=0, up_ratio=2)
        # self.fpn2_b = nn.Sequential(ConvBlock(in_channels = 768, out_channels = 256, kernel_size=1, stride=1, padding=0, name="fpn2_b_0", act='leakyrelu'),
        #                             ConvBlock(in_channels = 256, out_channels = 512, kernel_size=3, stride=1, padding=1, name="fpn2_b_1", act='leakyrelu'),
        #                             ConvBlock(in_channels = 512, out_channels = 256, kernel_size=1, stride=1, padding=0, name="fpn2_b_2", act='leakyrelu'),
        #                             ConvBlock(in_channels = 256, out_channels = 512, kernel_size=3, stride=1, padding=1, name="fpn2_b_3", act='leakyrelu'),
        #                             ConvBlock(in_channels = 512, out_channels = 256, kernel_size=1, stride=1, padding=0, name="fpn2_b_4", act='leakyrelu'))
        # self.fpn2_c = nn.Sequential(ConvBlock(in_channels = 256, out_channels = 512, kernel_size=3, stride=1, padding=1, name="fpn2_c_0", act='leakyrelu'),
        #                             ConvBlock(in_channels=512, out_channels=self.output_channels, kernel_size=1, stride=1, padding=0, name="fpn2_c_1", act='linear'))
        # #FPN3
        # self.fpn3 = nn.Sequential(ConvBlock(in_channels = 512, out_channels = 1024, kernel_size=3, stride=1, padding=1, name="fpn3_a", act='leakyrelu'),
        #                           ConvBlock(in_channels=1024, out_channels=self.output_channels, kernel_size=1, stride=1, padding=0, name='fpn3_b', act='linear'))
    
    def set_layer(self, layer_info):
        module_list = nn.Sequential()
        in_channels = []
        for layer_idx, info in enumerate(layer_info):
            print(layer_idx, info['type'])
            if info['type'] == "net":
                self.batch = int(info['batch']) if self.is_train else 1
                self.n_channels = int(info['channels'])
                self.in_width = int(info['width'])
                self.in_height = int(info['height'])
                self.n_classes = int(info['class'])
                module_list.add_module('layer_'+str(layer_idx)+'_net', nn.Sequential())
                in_channels.append(self.n_channels)
            elif info['type'] == "convolutional":
                module_list.add_module('layer_'+ str(layer_idx), make_conv_layer(layer_idx, info, in_channels[-1]))
                in_channels.append(int(info['filters']))
            elif info['type'] == 'shortcut':
                make_shortcut_layer(module_list, layer_idx)
                in_channels.append(in_channels[-1])
            elif info['type'] == 'route':
                module_list.add_module('layer_'+str(layer_idx)+'_route', nn.Sequential())
                layers = [int(y) for y in info["layers"].split(",")]
                if len(layers) == 1:
                    in_channels.append(in_channels[layers[0]])
                elif len(layers) == 2:
                    in_channels.append(in_channels[layers[0]] + in_channels[layers[1]])
            elif info['type'] == 'upsample':
                module_list.add_module('layer_'+str(layer_idx)+'_upsample',
                                       nn.Upsample(scale_factor=int(info['stride']), mode='nearest'))
                in_channels.append(in_channels[-1])
            elif info['type'] == 'yolo':
                module_list.add_module('layer_'+ str(layer_idx), YoloLayer(layer_idx, info, in_channels[-1], self.in_width, self.in_height))
                #make_yolo_layer(module_list, layer_idx, info, in_channels[-1])
                in_channels.append(in_channels[-1])
            #print(layer_idx, info['type'], in_channels[-1])
        return module_list            
    
    def initialize_weights(self):
        # track all layers
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_uniform_(m.weight)

                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

            elif isinstance(m, nn.Linear):
                nn.init.kaiming_uniform_(m.weight)
                nn.init.constant_(m.bias, 0)

    def transform_grid_data(self, features, fpn_idx):
        grid_w, grid_h= self.fpn_grid_size[fpn_idx*2: (fpn_idx+1)*2]
        width_per_grid, height_per_grid = self.stride[fpn_idx]
        _outs = []
        for b in range(self.box_per_anchor):
            offset = b * (5+self.n_classes)
            objness = torch.sigmoid(features[:,offset,:,:])
            box_xy = torch.sigmoid(features[:,offset+1:offset+3,:,:])
            box_w = self.anchor_size[b*2] * torch.exp(features[:,offset+3,:,:]) * width_per_grid
            box_h = self.anchor_size[b*2 + 1] * torch.exp(features[:,offset+4,:,:]) * height_per_grid
            for j in range(grid_h):
                for i in range(grid_w):
                    #objness = torch.sigmoid(features[:,offset,j,i])
                    box_x = (i+box_xy[:,0,j,i]) * width_per_grid
                    box_y = (j+box_xy[:,1,j,i]) * height_per_grid
                    #conf = self.softmax(features[:,offset+5:offset+5+self.n_classes,j,i])
                    conf = features[:,offset+5:offset+5+self.n_classes,j,i]
                    _out = torch.hstack((objness[:,j,i].reshape(self.batch,-1), box_x.reshape(self.batch,-1), box_y.reshape(self.batch,-1),
                                         box_w[:,j,i].reshape(self.batch,-1), box_h[:,j,i].reshape(self.batch,-1), conf.reshape(self.batch,-1)))
                    _outs.append(_out)
        out = torch.transpose(torch.stack(_outs),1,0)
        return out
    
    def get_grid_indexes(self, features):
        yv, xv = torch.meshgrid([torch.arange(features.shape[2]), torch.arange(features.shape[3])])
        grid_index = torch.stack((xv,yv), dim=2)
        grid_index = grid_index.view(1,grid_index.shape[0],grid_index.shape[1],2).cuda()
        return grid_index

    def convert_box_type(self, features, yololayer, yolo_idx):
        #get grid idexes
        grid_indexes = self.get_grid_indexes(features)
        #features = features.permute([0,2,3,1]).contiguous()
        height_per_grid, width_per_grid = self.get_grid_wh(yolo_idx)

        if not self.is_train:
            for a in range(self.box_per_anchor):
                #for each box in anchor
                feat = features[:, :, :, yololayer.box_attr*a:yololayer.box_attr*(a+1)]
                feat[:,:,:,0] = (torch.sigmoid(feat[:,:,:,0]) + grid_indexes[:,:,:,0]) * width_per_grid #x (tx + grid_idx)*grid_w
                feat[:,:,:,1] = (torch.sigmoid(feat[:,:,:,1]) + grid_indexes[:,:,:,1]) * height_per_grid #h (ty + grid_idx)*grid_h
                feat[:,:,:,2] = torch.exp(feat[:,:,:,2]) * yololayer.anchor[a][0]#w
                feat[:,:,:,3] = torch.exp(feat[:,:,:,3]) * yololayer.anchor[a][1]#h
                feat[:,:,:,4:] = torch.sigmoid(feat[:,:,:,4:]) #obj, cls 
        return features
    
    def get_grid_wh(self, grid_idx):
        grid_w, grid_h = self.fpn_grid_size[grid_idx * 2: (grid_idx + 1) * 2]
        w_per_grid, h_per_grid = self.in_width // grid_w, self.in_height // grid_h
        return w_per_grid, h_per_grid
    
    def get_loss(self, features):
        _loss = 0
        
        for f in features.shape[0]:
            for g in self.gt:
                features[f]
        
        return _loss 
    
    def forward(self, x):
        layer_result = []
        yolo_result = []
        for idx, (name, layer) in enumerate(zip(self.module_cfg, self.module_list)):
            #print(layer_result)
            if name['type'] == 'convolutional':
                x = layer(x)
                layer_result.append(x)
            elif name['type'] == 'shortcut':
                x = x + layer_result[int(name['from'])]
                layer_result.append(x)
            elif name['type'] == 'yolo':
                yolo_x = layer(x)
                #yolo_x = self.convert_box_type(yolo_x, layer, len(yolo_result))
                layer_result.append(yolo_x)
                yolo_result.append(yolo_x)
            elif name['type'] == 'upsample':
                x = layer(x)
                layer_result.append(x)
            elif name['type'] == 'route':
                layers = [int(y) for y in name["layers"].split(",")]
                x = torch.cat([layer_result[l] for l in layers], 1)
                layer_result.append(x)
        
        # x = self.conv1(x)
        # x = self.conv2(x)
        # x = self.resblock1(x)
        # x = self.conv3(x)
        # for i in range(2):
        #     x = self.resblock2(x)
        # x = self.conv4(x)
        # for i in range(8):
        #     x = self.resblock3(x)
        # #FPN1
        # block3_x = x

        # x = self.conv5(x)
        # for i in range(8):
        #     x = self.resblock4(x)
        # #FPN2
        # block4_x = x

        # x = self.conv6(x)
        # for i in range(4):
        #     x = self.resblock5(x)
        
        # x = self.conv7(x)
        # #FPN3
        # block5_1_x = x
        
        # #FPN2
        # fpn2_x = self.fpn2_a(x)
        # fpn2_x = torch.cat((fpn2_x, block4_x),dim=1)
        # fpn2_x = self.fpn2_b(fpn2_x)
        # fpn2_out = self.fpn2_c(fpn2_x)
        # #FPN1
        # fpn1_x = self.fpn1_a(fpn2_x)
        # fpn1_x = torch.cat((block3_x, fpn1_x),dim=1)
        # fpn1_x = self.fpn1_b(fpn1_x)
        # fpn1_out = self.fpn1_c(fpn1_x)
        # #FPN3
        # fpn3_out = self.fpn3(block5_1_x)
        
        # fpn3_data = self.transform_grid_data(fpn3_out,2)
        # fpn2_data = self.transform_grid_data(fpn2_out,1)
        # fpn1_data = self.transform_grid_data(fpn1_out,0)
        # pred_data = torch.cat((fpn1_data, fpn2_data, fpn3_data), dim = 1)
        return yolo_result



        
