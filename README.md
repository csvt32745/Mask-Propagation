# FTP-VM (CVPR2023)
Implementation and videos of **End-to-End Video Matting With Trimap Propagation** in CVPR2023.

[[CVPR OpenAccess]](https://openaccess.thecvf.com/content/CVPR2023/html/Huang_End-to-End_Video_Matting_With_Trimap_Propagation_CVPR_2023_paper.html) 
[[Presentation Video]](https://youtu.be/3jjA4nvUc8c)
[Paper PDF] 
[Supplementary Video]

![](assets/diagram.gif)

FTP-VM tries to integrate the trimap propagation and video matting into 1 model, and improves the efficiency. It can matte a 1024x576 video in 40 FPS on a RTX2080ti GPU, while previous works are in about 5 FPS.

- Given 1 or few pairs of memory trimaps and frames, FTP-VM is able to matte a video with arbitrary salient objects.
- We hope this work can encourage future research of fast universal video matting

# Roadmap
- [x] Clean the training code & data
- [x] Clean the dataset inference code & data
- [ ] Workaround for inference on TCVOM/OTVM
- [ ] Upload more supplementary videos
- [ ] (Possibly) Collaborate with SegmentAnyThing


# Installation
The version of pytorch used in our experiments is 1.8.2, but it should work with other versions.\
Install pytorch in the old version at https://pytorch.org/get-started/previous-versions \
Feel free to edit versions of packages for convenience.
```
pip install -r requirements.txt
```

# Model Usage
For those who want to use the model via code directly.
```python
import torch
from FTPVM.model import FastTrimapPropagationVideoMatting as FTPVM
model = FTPVM()
model.load_state_dict(torch.load('saves/ftpvm.pth'))
```
Usage
```python
# Images are in [0, 1] with size of (batch, time, channel, height, width)
# Memory has 1 frame per batch, and trimap (mask) has 1 channel.
query_imgs = torch.rand((2, 4, 3, 256, 256))
memory_imgs = torch.rand((2, 1, 3, 256, 256))
memory_trimaps = torch.rand((2, 1, 1, 256, 256))
# General forward
trimaps, boundary_mattes, full_mattes, recurrent_mems = model(query_imgs, memory_imgs, memory_trimaps)
# Forward with RNN memory
trimaps, boundary_mattes, full_mattes, recurrent_mems = model(query_imgs, memory_imgs, memory_trimaps, *recurrent_mems)
# Preserve memory key & values in Memory matching, which is useful in application
memory_key_val = model.encode_imgs_to_value(memory_imgs, memory_trimaps)
trimaps, boundary_mattes, full_mattes, recurrent_mems = model.forward_with_memory(query_imgs, *memory_key_val, *recurrent_mems)
```

# Inference
`inference_model_list.py` define the `(arbitrary name, model name in model/which_model.py, Inference class, model path)`\
Inference programs will use the defined name to find and load the model.

## Dataset
### Prepare
Please place the desired datasets into the same folder (or by  symbolic link).
- VM108: The same source as training data https://github.com/yunkezhang/TCVOM#videomatting108-dataset
- RVM
  - Download VM240k HD format at https://grail.cs.washington.edu/projects/background-matting-v2/#/datasets
  - Download DVM background video https://drive.google.com/file/d/1n2GMVnqJgihypwH_9IiHbhP9PWeCgpEt/view?usp=sharing and unzip it
  - Run 
    ```
    python generate_videomatte_with_background_video.py \
      --videomatte-dir ../dataset/VideoMatte240K_JPEG_HD/test \
      --background-dir ../dataset/dvm_bg \
      --out-dir ../dataset/videomatte_motion_1024 \
      --resize 1024 576 \
      --trimap_width 25
    ```
  - Edit parameters if you want to inference with different resolution
- Real Human Dataset: https://github.com/TiantianWang/VideoMatting-CRGNN

### Inference
The following files will be generated by default
- OUT_ROOT
  - DATASET_NAME
    - EXPERIMENT_NAME
      - DATASET_SUBNAME
        - clip1
          - pha
            - 0000.png
            - 0001.png
        - clip2
          - ...
        - clip1.mp4
        - clip2.mp4
      - MODLE_NAME.xlsx
    - EXPERIMENT_NAME2
      - ...
    - GT

For generel inference on datasets
```
usage: inference_dataset.py [-h] [--size SIZE] [--batch_size BATCH_SIZE] [--n_workers N_WORKERS]
                            [--gpu GPU] [--trimap_width TRIMAP_WIDTH] [--disable_video]
                            [--downsample_ratio DOWNSAMPLE_RATIO] [--out_root OUT_ROOT]
                            [--dataset_root DATASET_ROOT] [--disable_vm108] [--disable_realhuman]
                            [--disable_vm240k]

optional arguments:
  -h, --help            show this help message and exit
  --size SIZE           eval video size: sd, 1024, hd, 4k
  --batch_size BATCH_SIZE
                        frames in a batch
  --n_workers N_WORKERS
                        num workers
  --gpu GPU
  --trimap_width TRIMAP_WIDTH default=25
  --disable_video       Without savinig videos
  --downsample_ratio DOWNSAMPLE_RATIO default=1
  --out_root OUT_ROOT
  --dataset_root DATASET_ROOT
  --disable_vm108       Without VM108
  --disable_realhuman   Without RealHuman
  --disable_vm240k      Without VM240k
```

```
python inference_dataset.py --dataset_root ../dataset --out_root inference
```

For inference on VM108 with different memory update period
```
python inference_dataset_update_mem.py --dataset_root ../dataset --out_root inference  --memory_freq 30 60 120 240 480 1
```
`memory_freq`: Update memory in N frames. 1 for each frame, i.e. matting only.

## Webcam (Manual to be updated)
still not robust enough to webcam frames :(
```
python webcam.py
```
## Raw video
The code is borrowed from [RVM](https://github.com/PeterL1n/RobustVideoMatting)
```shell
usage: python inference_footages.py [-h] --root ROOT --out_root OUT_ROOT
                             [--gpu GPU] [--target_size TARGET_SIZE]
                             [--seq_chunk SEQ_CHUNK]
optional arguments:
  -h, --help            show this help message and exit
  --root ROOT           input video root
  --out_root OUT_ROOT   output video root
  --gpu GPU             gpu id, default = 0
  --target_size TARGET_SIZE
                        downsample the video by ratio of the larger width
                        to target_size, and upsampled back by FGF.
                        default = 1024
  --seq_chunk SEQ_CHUNK
                        frames to process in a batch
                        default = 4
```
You need to put 1 video with 1 thumbnail & trimap as memory pairs at least, where the thumbnail is suggested but not required to be the first frame.
More trimaps will generate different results.
```
- root
  - video1.mp4
  - video1_thumbnail.png
  - video1_trimap.png
  - video1_trimap2.png
  - ...
- out_root
  - video1__com.mp4
  - video1__fgr.mp4
  - video1__pha.mp4
  - video1_2_com.mp4
  - video1_2_fgr.mp4
  - video1_2_pha.mp4
  - ...
```
For more precised control, please refer to `inference_footages_util.py`.

## Workaround for inference on related works (TBD)
<details>
  <summary>TCVOM</summary>
</details>

<details>
  <summary>OTVM</summary>
  - Precomposite dataset by running `python .py`
  - Clone the repo
  - Copy `.py` into OTVM root folder, and run `python .py`
  - Evaluate by running`python evaluation/evaluate_lr.py`
</details>

# Training
## Dataset
Please put them in `dataset` folder at the same level (or symbolic link) as `FTP-VM` folder (this repo).
```
- dataset
  - Distinctions646
    - Train
  - VideoMatting108
    - BG_done
    - FG_done
    - ...
  - BG20k
    - BG-20k
  - YoutubeVIS
    - train

- FTP-VM (Model folder)
  - train.py
  - ...
```

- Image Matting Dataset: [D646](https://github.com/yuhaoliu7456/CVPR2020-HAttMatting)
- Video Matting Dataset: [VM108](https://github.com/yunkezhang/TCVOM#videomatting108-dataset)
- Video Object Segmentation Dataset: [YoutubeVIS 2019](https://youtube-vos.org/dataset/vis/)
- Background Image Dataset: [BG-20k](https://github.com/JizhiziLi/GFM)

If you just want to train on VM108 dataset, please read [VM108 dataset only](###VM108-dataset-only).

## Run

### Main setting
```shell
python train.py \
--id FTPVM \
--which_model FTPVM \
--num_worker 12 \
--benchmark \
--lr 0.0001 -i 120000 \
--iter_switch_dataset 30000 \
--use_background_dataset \
-b_seg 8 -b_img_mat 10 -b_vid_mat 4 \
-s_seg 8 -s_img_mat 4 -s_vid_mat 8 \
--seg_cd 20000 --seg_iter 10000 --seg_start 0 --seg_stop 100000 \
--size 480 \
--tvloss_type temp_seg_allclass_weight
```

### VM108 dataset only
```shell
python train.py \
--id FTPVM_VM108_only \
--which_model FTPVM \
--num_worker 12 \
--benchmark \
--lr 0.0001 -i 120000 \
--iter_switch_dataset 0 \
-b_vid_mat 4 -s_vid_mat 8 --seg_stop -1 \
--size 480 \
--tvloss_type temp_seg_allclass_weight
```


<details>
  <summary>Simple explanation</summary>

- `--id` : experiment name
- `--which_model` : defined model name in `model/which_model.py`
- `--use_background_dataset` : composite the data with an additional BG20k dataset as well
- `--iter_switch_dataset 30000` : switch to video dataset at N iter
- `-b_seg 8 -b_img_mat 10 -b_vid_mat 4` : batch size of datasets
- `-s_seg 8 -s_img_mat 4 -s_vid_mat 8` : sequence / clip length of datasets
- `--seg_cd 20000 --seg_iter 10000 --seg_start 0 --seg_stop 100000` : \
segmentation training starts at 0th iter, runs for 10000 iters followed by 20000-iters cooldown, stop at 100000th iter.
- `--tvloss_type` : variant of segmentation inconsistency loss
</details>

# Citation
```bibtex
@InProceedings{Huang_2023_CVPR,
    author    = {Huang, Wei-Lun and Lee, Ming-Sui},
    title     = {End-to-End Video Matting With Trimap Propagation},
    booktitle = {Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR)},
    month     = {June},
    year      = {2023},
    pages     = {14337-14347}
}
```
## Useful Implementation
- [RVM](https://github.com/PeterL1n/RobustVideoMatting)
  - Fast Guided Filter
  - Inference code
- [MiVOS](https://github.com/hkchengrex/MiVOS)
  - [STCN](https://github.com/hkchengrex/STCN)
  - [XMem](https://github.com/hkchengrex/XMem)
  - MiVOS inspired me to leverage video object segmentation on video matting (though it's been already applied in earlier researches)
  - Overall architecture
  - Their works are so amazing :)

# License
While the code is under GNU General Public License v3.0, the usage of pre-trained weight might be limited due to the training data.