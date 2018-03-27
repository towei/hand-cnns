import os
import copy

import cv2
import torch
import torchvision

from actiondatasets import smthg
from actiondatasets.epic import Epic
from actiondatasets.gteagazeplus import GTEAGazePlus
from actiondatasets.actiondataset import ActionDataset
from videotransforms import video_transforms, volume_transforms, tensor_transforms

from netraining.nets import c3d, c3d_adapt
from netraining.nets import i3d, i3d_adapt
from netraining.nets import i3dense, i3dense_adapt
from netraining.nets import i3res, i3res_adapt
from netraining.netscripts import features
from netraining.options import base_options, video_options, test_options
from netraining.utils import evaluation


def get_features(opt):
    cv2.setNumThreads(0)
    if opt.dataset == 'epic':
        crop_size = (128, 228)
    else:
        crop_size = 224

    # Modality params
    use_flow = False
    if opt.modality == 'flow':
        use_flow = True
        channel_nb = 2
    else:
        channel_nb = 3

    # Initialize transforms
    video_transform_list = [
        video_transforms.Resize(crop_size),
        volume_transforms.ClipToTensor(channel_nb=channel_nb)
    ]
    video_transform = video_transforms.Compose(video_transform_list)

    if opt.dataset == 'smthg':
        dataset = smthg.Smthg(
            rescale_flows=opt.rescale_flows,
            split=opt.split,
            use_flow=use_flow)
    elif opt.dataset == 'gteagazeplus_tres':
        all_subjects = ['Alireza', 'Carlos', 'Rahul', 'Yin', 'Shaghayegh']
        train_seqs, valid_seqs = evaluation.leave_one_out(
            all_subjects, opt.leave_out)
        dataset = GTEAGazePlus(
            label_type='rubicon',
            rescale_flows=opt.rescale_flows,
            seqs=valid_seqs,
            use_flow=use_flow,
            multi=opt.multi)
    elif opt.dataset == 'epic':
        dataset = Epic(opt.split)
    action_dataset = ActionDataset(
        dataset,
        clip_size=opt.clip_size,
        transform=video_transform,
        test=True,
        max_size=opt.mode_param)
    assert opt.batch_size == 1, (
        'During feature extraction '
        'batch size should be 1 bug got {}'.format(opt.batch_size))
    val_dataloader = torch.utils.data.DataLoader(
        action_dataset,
        shuffle=False,
        batch_size=opt.batch_size,
        num_workers=opt.threads)

    # Initialize C3D neural network
    if opt.network == 'c3d':
        c3dnet = c3d.C3D()
        model = c3d_adapt.C3DAdapt(
            opt, c3dnet, action_dataset.class_nb, in_channels=channel_nb)

    elif opt.network == 'i3d':
        if use_flow:
            i3dnet = i3d.I3D(class_nb=400, modality='flow', dropout_rate=0.5)
            model = i3d_adapt.I3DAdapt(opt, i3dnet, action_dataset.class_nb)
        else:
            i3dnet = i3d.I3D(class_nb=400, modality='rgb', dropout_rate=0.5)
            model = i3d_adapt.I3DAdapt(
                opt, i3dnet, action_dataset.class_nb, in_channels=channel_nb)
    elif opt.network == 'i3dense':
        densenet = torchvision.models.densenet121(pretrained=True)
        i3densenet = i3dense.I3DenseNet(
            copy.deepcopy(densenet), opt.clip_size, inflate_block_convs=True)
        model = i3dense_adapt.I3DenseAdapt(
            opt, i3densenet, action_dataset.class_nb, channel_nb=channel_nb)
    elif opt.network == 'i3res':
        resnet = torchvision.models.resnet50(pretrained=True)
        i3resnet = i3res.I3ResNet(resnet, frame_nb=opt.clip_size)
        model = i3res_adapt.I3ResAdapt(
            opt,
            i3resnet,
            class_nb=action_dataset.class_nb,
            channel_nb=channel_nb)
    else:
        raise ValueError(
            'network should be in [i3res|i3dense|i3d|c3d but got {}]').format(
                opt.network)

    optimizer = torch.optim.SGD(model.net.parameters(), lr=1)

    model.set_optimizer(optimizer)

    # Use multiple GPUS
    if opt.gpu_parallel:
        available_gpus = torch.cuda.device_count()
        device_ids = list(range(opt.gpu_nb))
        print('Using {} out of {} available GPUs'.format(
            len(device_ids), available_gpus))
        model.net = torch.nn.DataParallel(model.net, device_ids=device_ids)

    # Load existing weights
    model.net.eval()
    if opt.use_gpu:
        model.net.cuda()
    model.load(
        load_path=opt.checkpoint_path,
        features=True)  # TODO remove features=True

    features.get_features(val_dataloader, model, opt=opt)


if __name__ == '__main__':
    # Initialize base options
    options = base_options.BaseOptions()

    # Add test options and parse
    test_options.add_test_options(options)
    video_options.add_video_options(options)
    opt = options.parse()
    get_features(opt)
