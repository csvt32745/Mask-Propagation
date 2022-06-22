from functools import lru_cache
from typing import Optional
import torch
from torch import Tensor
import numpy as np
import cv2
from torch import nn
from torchvision.ops.deform_conv import DeformConv2d
from einops import rearrange
import kornia as K
from functools import reduce

from .STCN.network import *
from .STCN.modules import *

from .recurrent_decoder import ConvGRU



class FeatureFusion(nn.Module):
    def __init__(self, indim, outdim):
        super().__init__()

        self.block1 = ResBlock(indim, outdim)
        self.attention = cbam.CBAM(outdim)
        self.block2 = ResBlock(outdim, outdim)

    def forward_single_frame(self, x):
        # x = torch.cat([x, f16], 1)
        x = self.block1(x)
        r = self.attention(x)
        x = self.block2(x + r)
        return x
    
    def forward_time_series(self, x):
        B, T = x.shape[:2]
        x = self.forward_single_frame(x.flatten(0, 1)).unflatten(0, (B, T))
        return x
    
    def forward(self, x):
        if x.ndim == 5:
            return self.forward_time_series(x)
        else:
            return self.forward_single_frame(x)

class FeatureFusion2(FeatureFusion):
    def __init__(self, indim, outdim):
        super().__init__(indim, outdim)
        self.attention = cbam.CBAM(indim)
        self.block2 = ResBlock(indim, outdim)

    def forward_single_frame(self, x):
        x = self.attention(x)
        x = self.block2(x)
        return x
    
    def forward_time_series(self, x):
        B, T = x.shape[:2]
        x = self.forward_single_frame(x.flatten(0, 1)).unflatten(0, (B, T))
        return x
    
    def forward(self, x):
        if x.ndim == 5:
            return self.forward_time_series(x)
        else:
            return self.forward_single_frame(x)

class FeatureFusion3(FeatureFusion):
    def __init__(self, indim, outdim):
        super().__init__(indim, outdim)
        self.block = ResBlock(indim, outdim)
        self.attention = cbam.CBAM(outdim)

    def forward_single_frame(self, x):
        x = self.block(x)
        return x + self.attention(x)
    
    def forward_time_series(self, x):
        B, T = x.shape[:2]
        x = self.forward_single_frame(x.flatten(0, 1)).unflatten(0, (B, T))
        return x
    
    def forward(self, x):
        if x.ndim == 5:
            return self.forward_time_series(x)
        else:
            return self.forward_single_frame(x)


class SingleDeformConv2d(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size=3, stride=1,
                 padding=1, dilation=1, groups=1, offset_group=1):
        super().__init__()
        offset_channels = 2 * kernel_size * kernel_size
        self.conv_offset = nn.Conv2d(
            in_channels,
            offset_channels * offset_group,
            kernel_size = kernel_size,
            stride = stride,
            padding = padding,
            dilation = dilation,
        )
        self.DCN_V1 = DeformConv2d(
            in_channels,
            out_channels,
            kernel_size = kernel_size,
            stride = stride,
            padding = padding,
            dilation = dilation,
            groups = groups,
            bias = False
        )
    def forward(self, x):
        offset = self.conv_offset(x)
        return self.DCN_V1(x, offset)

class AlignDeformConv2d(nn.Module):
    def __init__(self, mem_channels, que_channels, out_channels, kernel_size=3, stride=1,
                 padding=1, dilation=1, groups=1, offset_group=1):
        super().__init__()
        n_k = kernel_size * kernel_size * offset_group
        offset_channels = 3 * n_k
        self.split = [n_k*2, n_k]
        self.n_k = n_k
        self.conv_offset = nn.Conv2d(
            mem_channels+que_channels,
            offset_channels,
            kernel_size = kernel_size,
            stride = stride,
            padding = padding,
            dilation = dilation,
        )
        self.dfconv2d = DeformConv2d(
            mem_channels,
            out_channels,
            kernel_size = kernel_size,
            stride = stride,
            padding = padding,
            dilation = dilation,
            groups = groups,
            bias = False
        )

    def forward_single_frame(self, mem, que):
        offset, mask = self.conv_offset(torch.cat([mem, que], dim=1)).split(self.split, dim=1)
        return self.dfconv2d(mem, offset, mask)
    
    def forward_time_series(self, mem, que):
        B, T = mem.shape[:2]
        x = self.forward_single_frame(mem.flatten(0, 1), que.flatten(0, 1)).unflatten(0, (B, T))
        return x
    
    def forward(self, *args):
        if args[0].ndim == 5:
            return self.forward_time_series(*args)
        else:
            return self.forward_single_frame(*args)



class ConvSelfAttention(nn.Module):
    def __init__(self, dim=32, attn_dim=32, head=2, qkv_bias=False, patch_size=16, drop_p=0.25):
        super().__init__()
        # (b*t, 256, H/4, W/4)
        
        def check_size(patch_sz):
            patch_sz = patch_sz
            length = 0
            while patch_sz > 1:
                assert (patch_sz&1) == 0, 'patch_size is required to be 2^N'
                patch_sz = patch_sz >> 1
                length += 1
            return length
        length = check_size(int(patch_size))
        
        def get_convstem(ch_in, ch_out, length):
            chs = [ch_in] + [ch_out]*length
            # net = [Deform_Conv_V1(ch_in, ch_in, 3, 1, 1)]
            net = []
            for i in range(length):
                net += [
                    nn.Conv2d(chs[i], chs[i+1], 3, 1, 1),
                    nn.MaxPool2d(2, 2),
                    nn.ReLU(True),
                ]
            net.append(nn.Conv2d(chs[-1], ch_out, 1))
            return nn.Sequential(*net) # abort the last act
        
        self.kernel, self.stride, self.padding = (patch_size, patch_size, 0)
        self.conv_q = get_convstem(dim, attn_dim*head, length)
        self.conv_k = get_convstem(dim, attn_dim*head, length)
        # self.conv_k = self.conv_q
        # (b*t, qkv, H', W')
        self.is_proj_v = head > 1
        self.conv_v = nn.Conv2d(dim, dim*head, 1, bias=qkv_bias) if self.is_proj_v else nn.Identity()
        self.head = head
        self.ch_qkv = attn_dim
        self.merge_v = nn.Conv2d(dim*self.head, dim, 1) if self.is_proj_v else nn.Identity()
        self.qk_scale = dim ** -0.5
        self.patch_size = patch_size
        self.unfold = nn.Unfold(kernel_size=self.kernel, stride=self.stride, padding=self.padding)
        # print(self.unfold)
        # self.drop_attn = nn.Dropout(p=drop_p)
        # self.drop_proj = nn.Dropout(p=drop_p)

    
    @staticmethod
    @lru_cache(maxsize=None)
    def get_diag_id(length: int):
        return list(range(length))

    def forward(self, x_query, x_key=None, extra_value=None):
        
        b, t, c, h, w = x_query.shape
        if x_key is None:
            x_key = x_query
        # t_kv = x_kv.size(1)

        x_query = x_query.flatten(0, 1)
        x_key = x_key.flatten(0, 1)
        x_value = x_key
        if extra_value is not None:
            x_value = torch.cat([x_value, extra_value.flatten(0, 1)], dim=1)
        
        v = self.conv_v(x_value) # ((b t), ...)
        v = self.unfold(v) # ((b t) P*P*c*m h'*w')
        v = rearrange(v, '(b t) (m c) w -> b m (t w) c', b=b, m=self.head)

        q = rearrange(self.conv_q(x_query), "(b t) (m c) h w -> b m c (t h w)", b=b, m=self.head, c=self.ch_qkv)
        k = rearrange(self.conv_k(x_key), "(b t) (m c) h w -> b m c (t h w)", b=b, m=self.head, c=self.ch_qkv)

        A = q.transpose(-2, -1) @ k
        # exclude self
        # i = self.get_diag_id(A.size(-1)) 
        # A[..., i, i] = -torch.inf
        A = A.softmax(dim=-1) # b m (t hq wq) (t hk wk)

        out = A @ v  # b m (t hq wq) c
        out = rearrange(out, 'b m (t w) c -> (b t m) c w', t=t)
        out = F.fold(out, (h, w), kernel_size=self.kernel, stride=self.stride, padding=self.padding)
        out = rearrange(out, '(b t m) c h w -> (b t) (c m) h w', b=b, m=self.head)
        out = self.merge_v(out)
        out = rearrange(out, '(b t) c h w -> b t c h w', b=b)
        return out, A

class AlignedSelfAttention(ConvSelfAttention):
    def __init__(self, dim=32, attn_dim=32, head=2, qkv_bias=False, patch_size=16, drop_p=0.25):
        super().__init__(dim, attn_dim, head, qkv_bias, patch_size, drop_p)
    
    def forward(self, x_query, x_memory=None, mask=None):
        b, t, c, h, w = x_query.shape
        x_kv = x_query if x_memory is None else x_memory
        # t_kv = x_kv.size(1)

        x_kv = x_kv.flatten(0, 1)
        x_query = x_query.flatten(0, 1)
        v = self.conv_v(x_kv) # ((b t), ...)
        v = self.unfold(v) # ((b t) P*P*c*m h'*w')
        v = rearrange(v, '(b t) (m c) w -> b m (t w) c', b=b, m=self.head)

        q = rearrange(self.conv_q(x_query), "(b t) (m c) h w -> b m c (t h w)", b=b, m=self.head, c=self.ch_qkv)
        k = rearrange(self.conv_k(x_kv), "(b t) (m c) h w -> b m c (t h w)", b=b, m=self.head, c=self.ch_qkv)

        A = q.transpose(-2, -1) @ k
        # exclude self
        # i = self.get_diag_id(A.size(-1)) 
        # A[..., i, i] = -torch.inf
        A = A.softmax(dim=-1) # b m (t hq wq) (t hk wk)

        out = A @ v  # b m (t hq wq) c
        out = rearrange(out, 'b m (t w) c -> (b t m) c w', t=t)
        out = F.fold(out, (h, w), kernel_size=self.kernel, stride=self.stride, padding=self.padding)
        out = rearrange(out, '(b t m) c h w -> (b t) (c m) h w', b=b, m=self.head)
        out = self.merge_v(out)
        out = rearrange(out, '(b t) c h w -> b t c h w', b=b)
        return out, A

class DeformableConvGRU(ConvGRU):
    def __init__(self, channels: int, kernel_size: int = 3, padding: int = 1):
        super().__init__(channels, kernel_size, padding)
        self.dfconv = AlignDeformConv2d(channels, channels, channels)

    def forward_single_frame(self, x, h):
        h = self.dfconv(h, x)
        return super().forward_single_frame(x, h)
    
    def forward_time_series(self, x, h):
        o = []
        for xt in x.unbind(dim=1):
            ot, h = self.forward_single_frame(xt, h)
            o.append(ot)
        o = torch.stack(o, dim=1)
        return o, h
        
    def forward(self, x, h: Optional[torch.Tensor]):
        if h is None:
            h = torch.zeros((x.size(0), x.size(-3), x.size(-2), x.size(-1)),
                            device=x.device, dtype=x.dtype)
        
        if x.ndim == 5:
            return self.forward_time_series(x, h)
        else:
            return self.forward_single_frame(x, h)
    
class DeformableFrameAlign(nn.Module):
    def __init__(self, channels: int):
        super().__init__()
        self.dfconv = AlignDeformConv2d(channels, channels, channels)
        self.fuse = FeatureFusion2(channels*2, channels)

    def forward_single_frame(self, x, h):
        return torch.cat([x, self.dfconv(x, x)], dim=1), h
        # return self.fuse(torch.cat([x, self.dfconv(x, x)], dim=1)), h
    
    def forward_time_series(self, x, h):
        if h is None:
            h = x[:, [0]]
        xx = torch.cat([h, x[:, :-1]], dim=1) # b t c h w
        return self.fuse(torch.cat([x, self.dfconv(xx, x)], dim=2)), x[:, [-1]]
        
    def forward(self, x, h: Optional[torch.Tensor]):
        # if h is None:
        #     h = torch.zeros((x.size(0), x.size(-3), x.size(-2), x.size(-1)),
        #                     device=x.device, dtype=x.dtype)
                            
        if x.ndim == 5:
            return self.forward_time_series(x, h)
        else:
            return self.forward_single_frame(x, h)

class PRM(nn.Module):
    def __init__(self, eps=1e-5):
        super().__init__()
        self.eps = eps
        
        # self.ker_dilate = [None] + [cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (i, i)) for i in range(1, 31)]
        self.ker_dilate = [None] + [torch.ones((i, i)).cuda() for i in range(1, 16)]
        
    def forward_single_frame(self, small, large, dilate_width, sigmoid):
        up = F.interpolate(small, size=large.shape[-2:])
        # B, T, 1, H, W
        
        trans = torch.sigmoid(up) if sigmoid else up
        trans = ((trans > self.eps) & (trans < 1-self.eps)).float()
        # trans = ((up > self.eps) & (up < 1-self.eps)).detach().cpu().numpy()[:, :, 0]
        # B, T, H, W -> B*T, H, W
        # trans = trans.permute(B)
        # b, t = trans.shape[:2]
        if dilate_width >= 1:
            trans = K.morphology.dilation(trans, self.ker_dilate[dilate_width], engine='convolution')
        return trans*large + (1-trans)*up

    def forward(self, small, large, dilate_width=0, sigmoid=False):
        if small.ndim == 5:
            B, T = small.shape[:2]
            return self.forward_single_frame(
                small.flatten(0, 1), 
                large.flatten(0, 1), 
                dilate_width,
                sigmoid
            ).unflatten(0, (B, T))
        return self.forward_single_frame(small, large, dilate_width, sigmoid)

class GlobalMatch(nn.Module):
    def __init__(self, stride=4):
        super().__init__()
        self.stride=stride
        self.pool = nn.Unfold(1, stride=stride)
        # self.pool = nn.Sequential(
        #     nn.AvgPool2d((stride, stride)),
        #     nn.Flatten(-2, -1),
        #     )
        

    def compute_scores_seq(self, a, b):
        # B, N, CH
        # min scores
        l2_dist, min_idx = torch.pairwise_distance(a.unsqueeze(-2), b.unsqueeze(-3)).min(dim=-1)
        # l2_dist = l2_dist.clamp_min(0)
        # print(min_idx)
        # print(min_idx)
        # print(b[min_idx].shape)
        # l2_dist = 1-2/(1+torch.exp(l2_dist))
        l2_dist = 2*torch.sigmoid(-l2_dist)
        return l2_dist
    
    def _pool(self, x):
        if x.ndim == 5:
            B, T = x.shape[:2]
            return self.pool(x.flatten(0, 1)).unflatten(0, (B, T))
        return self.pool(x)

    def forward(self, a,  b):
        if a.ndim == 3:
            return self.compute_scores_seq(a, b)
        oh, ow = a.shape[-2:]

        a = self._pool(a)
        h, w = oh//self.stride, ow//self.stride
        a = a.transpose(-1, -2)
        # a = a.flatten(-2, -1).transpose(-1, -2)
        b = self._pool(b).transpose(-1, -2)
        
        scores = self.compute_scores_seq(a, b)
        # print(scores.shape, a.shape, b.shape, (h, w), (oh, ow))
        return F.interpolate(scores.unflatten(-1, (h, w)), size=(oh, ow), mode='nearest').unsqueeze(-3)
        

class ChannelAttention(nn.Module):
    def __init__(self, ch_in, ch_out):
        super().__init__()
        self.fc = nn.Sequential(
            nn.Linear(ch_in, ch_out, bias=False),
            # nn.ReLU(inplace=True),
            # nn.Linear(channel // reduction, channel, bias=False),
            nn.Sigmoid()
        )

    def forward(self, feat, take_mean=False):
        if take_mean:
            size = feat.shape[:-3]
            return self.fc(feat.mean(dim=(-2, -1))).view(*size, -1, 1, 1)
        return self.fc(feat)

class LRASPP(nn.Module):
    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.aspp1 = nn.Sequential(
            nn.Conv2d(in_channels, out_channels, 1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(True)
        )
        self.aspp2 = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Conv2d(in_channels, out_channels, 1, bias=False),
            nn.Sigmoid()
        )
        
    def forward_single_frame(self, x):
        return self.aspp1(x) * self.aspp2(x)
    
    def forward_time_series(self, x):
        B, T = x.shape[:2]
        x = self.forward_single_frame(x.flatten(0, 1)).unflatten(0, (B, T))
        return x
    
    def forward(self, x):
        if x.ndim == 5:
            return self.forward_time_series(x)
        else:
            return self.forward_single_frame(x)

class PSP(nn.Module):
    def __init__(self, features, per_features, out_features, sizes=(1, 2, 4, 8)):
        super().__init__()
        self.stages = []
        self.stages = nn.ModuleList([self._make_stage(features, per_features, size) for size in sizes])
        self.bottleneck = nn.Conv2d(per_features * len(sizes) + features, out_features, kernel_size=1)
        self.relu = nn.ReLU()

    def _make_stage(self, features, per_features, size):
        prior = nn.AdaptiveAvgPool2d(output_size=(size, size))
        conv = nn.Conv2d(features, per_features, kernel_size=1, bias=False)
        return nn.Sequential(prior, conv)

    def _forward(self, feats):
        h, w = feats.size(2), feats.size(3)
        priors = [F.interpolate(input=stage(feats), size=(h, w), mode='bilinear', align_corners = True) for stage in self.stages] + [feats]
        bottle = self.bottleneck(torch.cat(priors, 1))
        return self.relu(bottle)
    
    def forward(self, x):
        if x.ndim == 5:
            B, T = x.shape[:2]
            return self._forward(x.flatten(0, 1)).unflatten(0, (B, T))
        else:
            return self._forward(x)

class FocalModulation(nn.Module):
    def __init__(self, dim, focal_window, focal_level, focal_factor=2, bias=True, proj_drop=0., use_postln=False):
        super().__init__()

        self.dim = dim
        self.focal_window = focal_window
        self.focal_level = focal_level
        self.focal_factor = focal_factor
        self.use_postln = use_postln

        self.q = nn.Linear(dim, dim, bias=bias)
        self.f = nn.Linear(dim, dim + (self.focal_level+1), bias=bias)
        self.h = nn.Conv2d(dim, dim, kernel_size=1, stride=1, bias=bias)

        self.act = nn.GELU()
        self.proj = nn.Linear(dim, dim)
        self.proj_drop = nn.Dropout(proj_drop)
        self.focal_layers = nn.ModuleList()
                
        self.kernel_sizes = []
        for k in range(self.focal_level):
            kernel_size = self.focal_factor*k + self.focal_window
            self.focal_layers.append(
                nn.Sequential(
                    nn.Conv2d(dim, dim, kernel_size=kernel_size, stride=1, 
                    groups=dim, padding=kernel_size//2, bias=False),
                    nn.GELU(),
                    )
                )              
            self.kernel_sizes.append(kernel_size)          
        if self.use_postln:
            self.ln = nn.LayerNorm(dim)

    def forward(self, x, v=None):
        """
        Args:
            x: input features with shape of (B, C, H, W)
        """
        C = x.size(1)
        x = x.permute(0, 2, 3, 1)
        v = x if v is None else v.permute(0, 2, 3, 1)
        
        # pre linear projection
        v = self.f(v).permute(0, 3, 1, 2).contiguous() # for feat aggr
        q = self.q(x).permute(0, 3, 1, 2).contiguous()
        ctx, self.gates = torch.split(v, (C, self.focal_level+1), 1)
        
        # context aggreation
        ctx_all = 0 
        for l in range(self.focal_level):         
            ctx = self.focal_layers[l](ctx)
            ctx_all = ctx_all + ctx*self.gates[:, l:l+1]
        ctx_global = self.act(ctx.mean(2, keepdim=True).mean(3, keepdim=True))
        ctx_all = ctx_all + ctx_global*self.gates[:,self.focal_level:]

        # focal modulation
        modulator = self.h(ctx_all)
        out = q*modulator
        out = out.permute(0, 2, 3, 1).contiguous()
        if self.use_postln:
            out = self.ln(out)
        
        # post linear porjection
        out = self.proj(out)
        out = self.proj_drop(out).permute(0, 3, 1, 2)
        return out

class AttnGRU(nn.Module):
    def __init__(self,
                 channels: int,
                 kernel_size: int = 3,
                 padding: int = 1,
                 hidden = 16,
                 patch_size=9,
                 head=1):
        super().__init__()
        self.channels = channels
        self.ih = nn.Sequential(
            nn.Conv2d(channels * 2, channels * 2, kernel_size, padding=padding),
            nn.Sigmoid()
        )
        self.hh = nn.Sequential(
            nn.Conv2d(channels * 2, channels, kernel_size, padding=padding),
            nn.Tanh()
        )
        self.attn = SoftCrossAttention(channels, hidden, head, patch_size=patch_size, is_proj_v=True)
        
    def forward_single_frame(self, x, h):
        h = (h + torch.tanh(self.attn(x, h)))*0.5
        r, z = self.ih(torch.cat([x, h], dim=1)).split(self.channels, dim=1)
        c = self.hh(torch.cat([x, r * h], dim=1))
        h = (1 - z) * h + z * c
        return h, h
    
    def forward_time_series(self, x, h):
        o = []
        for xt in x.unbind(dim=1):
            ot, h = self.forward_single_frame(xt, h)
            o.append(ot)
        o = torch.stack(o, dim=1)
        return o, h
        
    def forward(self, x, h: Optional[Tensor]):
        if h is None:
            h = torch.zeros((x.size(0), x.size(-3), x.size(-2), x.size(-1)),
                            device=x.device, dtype=x.dtype)
        
        if x.ndim == 5:
            return self.forward_time_series(x, h)
        else:
            return self.forward_single_frame(x, h)

class FocalGRU(ConvGRU):
    def __init__(self, channels: int, kernel_size: int = 3, padding: int = 1):
        super().__init__(channels, kernel_size, padding)
        
        self.focal = FocalModulation(channels, 5, 4)
        
    def forward_single_frame(self, x, h):
        h = self.focal(h, x)
        r, z = self.ih(torch.cat([x, h], dim=1)).split(self.channels, dim=1)
        c = self.hh(torch.cat([x, r * h], dim=1))
        h = (1 - z) * h + z * c
        return h, h

class FocalGRUFix(ConvGRU):
    def __init__(self, channels: int, kernel_size: int = 3, padding: int = 1):
        super().__init__(channels, kernel_size, padding)
        
        self.focal = FocalModulation(channels, 5, 4)
        
    def forward_single_frame(self, x, h):
        h = (h+torch.tanh(self.focal(h, x)))*0.5
        r, z = self.ih(torch.cat([x, h], dim=1)).split(self.channels, dim=1)
        c = self.hh(torch.cat([x, r * h], dim=1))
        h = (1 - z) * h + z * c
        return h, h


class SoftSplit(nn.Module):
    def __init__(self, channel, hidden, kernel_size, stride, padding, dropout=0.):
        super(SoftSplit, self).__init__()
        self.kernel_size = kernel_size
        self.t2t = nn.Unfold(kernel_size=kernel_size, stride=stride, padding=padding)
        c_in = reduce((lambda x, y: x * y), kernel_size) * channel
        self.embedding = nn.Linear(c_in, hidden) if hidden > 0 else nn.Identity()
        self.dropout = nn.Dropout(p=dropout)

    def forward(self, x):
        # B, C, H, W
        feat = self.t2t(x) # B, C*K*K, P
        feat = feat.permute(0, 2, 1)
        feat = self.embedding(feat) # B, P, C'
        feat = self.dropout(feat)
        # B, P, C'
        return feat

class SoftComp(nn.Module):
    def __init__(self, channel, hidden, kernel_size, stride, padding):
        super(SoftComp, self).__init__()
        self.kernel_size = kernel_size
        self.stride = stride
        self.padding = padding
        self.relu = nn.LeakyReLU(0.2, inplace=True)
        c_out = reduce((lambda x, y: x * y), kernel_size) * channel
        self.embedding = nn.Linear(hidden, c_out) if hidden > 0 else nn.Identity()
        # self.bias = nn.Parameter(torch.zeros((channel, h, w), dtype=torch.float32), requires_grad=True)

    def forward(self, x, out_size):
        # B, P, C'
        feat = self.embedding(x) # B, P, C*K*K
        feat = feat.permute(0, 2, 1)
        feat = F.fold(feat, output_size=out_size, kernel_size=self.kernel_size, stride=self.stride, padding=self.padding)
        # B, C, H, W
        return feat

class SoftCrossAttention(nn.Module):
    def __init__(self, 
            dim=32, 
            hidden=32,
            head=2,
            patch_size=9,
            is_proj_v=False
        ):
        super().__init__()
        self.head = head
        self.is_proj_v = head > 1 or is_proj_v
        hidden_v = hidden if self.is_proj_v else -1
        self.kernel, self.stride, self.padding = [(i, i) for i in [patch_size, patch_size//2, patch_size//2]]
        self.ss_k = SoftSplit(dim, hidden, self.kernel, self.stride, self.padding)
        self.ss_v = SoftSplit(dim, hidden_v, self.kernel, self.stride, self.padding)
        self.sc = SoftComp(dim, hidden_v, self.kernel, self.stride, self.padding)
        
        self.proj_k = nn.Linear(hidden, hidden*head)
        self.proj_v = nn.Linear(hidden, hidden*head) if self.is_proj_v else nn.Identity()
        self.proj_out = nn.Linear(hidden*head, hidden) if self.is_proj_v else nn.Identity()
        self.patch_size = patch_size

    def _forward(self, x_query, x_key, x_value=None):
        size = x_query.shape[-2:]
        q = self.ss_k(x_query) # b*t, p, c
        k = self.ss_k(x_key)
        v = self.ss_v(x_value)
        
        # b*t, m, p, c
        q = rearrange(self.proj_k(q), 'b p (m c) -> b m p c', m=self.head)
        k = rearrange(self.proj_k(k), 'b p (m c) -> b m p c', m=self.head)
        v = rearrange(self.proj_v(v), 'b p (m c) -> b m p c', m=self.head)
        
        A = q @ k.transpose(-2, -1) # b*t, m, p, p
        A = A.softmax(dim=-1)
        out = A @ v  # b*t, m, p, c
        out = self.proj_out(rearrange(out, 'b m p c -> b p (m c)'))
        out = self.sc(out, size) # b*t, c, h, w

        return out
    
    def forward(self, x_query, x_key, x_value=None):
        if x_query.ndim == 5:
            b, t = x_query.shape[:2]
            x_query = x_query.flatten(0, 1)
            x_key = x_key.flatten(0, 1)
            x_value = x_key if x_value is None else x_value.flatten(0, 1)
            return self._forward(x_query, x_key, x_value).unflatten(0, (b, t))
        if x_value is None:
            x_value = x_key
        return self._forward(x_query, x_key, x_value)

class GatedConv2d(nn.Module):
    def __init__(self, ch_in, ch_out, kernel=1, stride=1, padding=0, act=nn.LeakyReLU(0.1, True)):
        super().__init__()
        self.conv = nn.Conv2d(ch_in, ch_out*2, kernel, stride, padding)
        self.ch_out = ch_out
        self.act = act
    
    def forward(self, x):
        if x.ndim == 5:
            b, t = x.shape[:2]
            x, m = self.conv(x.flatten(0, 1)).split(self.ch_out, dim=1)
            return (self.act(x)*torch.sigmoid(m)).unflatten(0, (b, t))
            
        x, m = self.conv(x).split(self.ch_out, dim=1)
        return self.act(x)*torch.sigmoid(m)