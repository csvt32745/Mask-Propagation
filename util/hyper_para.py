from argparse import ArgumentParser


def none_or_default(x, default):
    return x if x is not None else default

class HyperParameters():
    def parse(self, unknown_arg_ok=False):
        parser = ArgumentParser()
        parser.add_argument('--which_model', help='Use which model', default='NULL')

        # Enable torch.backends.cudnn.benchmark -- Faster in some cases, test in your own environment
        parser.add_argument('--benchmark', action='store_true')
        parser.add_argument('--num_worker', help='num_workers of dataloader', default=16, type=int)
        parser.add_argument('--nb_frame_only', help='neighbor frames only', action='store_true')
        parser.add_argument('--dataset_trimap', help='compute trimap in dataset', action='store_true')
        parser.add_argument('--perturb_mask', help='compute perturn mask in dataset', action='store_true')
        parser.add_argument('--get_bgr_pha', help='get pha of bgr by lastframe affine', action='store_true')
        parser.add_argument('--size', help='dataset img size', default=256, type=int)

        # Data parameters
        parser.add_argument('--static_root', help='Static training data root', default='../static')
        parser.add_argument('--bl_root', help='Blender training data root', default='../BL30K')
        parser.add_argument('--yv_root', help='YouTubeVOS data root', default='../YouTube')
        parser.add_argument('--davis_root', help='DAVIS data root', default='../DAVIS')

        parser.add_argument('--stage', help='Training stage (0-static images, 1-Blender dataset, 2-DAVIS+YouTubeVOS)', type=int, default=0)
        parser.add_argument('--iter_switch_dataset', help='switch to vid dataset at which epoch', type=int, default=5)


        # Generic learning parameters
        parser.add_argument('--long_seq', help='Adjust seq length and batch size', action='store_true', default=False)

        parser.add_argument('-b', '--batch_size', help='Default is dependent on the training stage, see below', default=None, type=int)
        parser.add_argument('--seq_length', help='sequence size', default=4, type=int)
        parser.add_argument('-i', '--iterations', help='Default is dependent on the training stage, see below', default=None, type=int)
        parser.add_argument('--steps', help='Default is dependent on the training stage, see below', nargs="*", default=None, type=int)
        parser.add_argument('--seg_start', help='Start iter of segmentation training', default=0, type=int)
        parser.add_argument('--seg_cd', help='Cooldown of segmentation training', default=20000, type=int)
        parser.add_argument('--seg_iter', help='Iter of segmentation training', default=10000, type=int)
        parser.add_argument('--seg_stop', help='At which iter to stop segmentation training', default=80000, type=int)

        parser.add_argument('--lr', help='Initial learning rate', default=1e-4, type=float)
        parser.add_argument('--gamma', help='LR := LR*gamma at every decay step', default=0.1, type=float)

        # Loading
        parser.add_argument('--load_network', help='Path to pretrained network weight only')
        parser.add_argument('--load_model', help='Path to the model file, including network, optimizer and such')

        # Logging information
        parser.add_argument('--id', help='Experiment UNIQUE id, use NULL to disable logging to tensorboard', default='NULL')
        parser.add_argument('--debug', help='Debug mode which logs information more often', action='store_true')

        # Multiprocessing parameters, not set by users
        parser.add_argument('--local_rank', default=0, type=int, help='Local rank of this process')

        if unknown_arg_ok:
            args, _ = parser.parse_known_args()
            self.args = vars(args)
        else:
            self.args = vars(parser.parse_args())

        # Stage-dependent hyperparameters
        # Assign default if not given
        if self.args['stage'] == 0:
            self.args['batch_size'] = none_or_default(self.args['batch_size'], 7)
            self.args['iterations'] = none_or_default(self.args['iterations'], 300000)
            self.args['steps'] = none_or_default(self.args['steps'], [250000])
            # self.args['single_object'] = True
        elif self.args['stage'] == 1:
            self.args['batch_size'] = none_or_default(self.args['batch_size'], 4)
            self.args['iterations'] = none_or_default(self.args['iterations'], 500000)
            self.args['steps'] = none_or_default(self.args['steps'], [450000])
            # self.args['single_object'] = False
        elif self.args['stage'] == 2:
            self.args['batch_size'] = none_or_default(self.args['batch_size'], 4)
            self.args['iterations'] = none_or_default(self.args['iterations'], 150000)
            self.args['steps'] = none_or_default(self.args['steps'], [125000])
            # self.args['single_object'] = False
        else:
            self.args['batch_size'] = none_or_default(self.args['batch_size'], 8)
            self.args['iterations'] = none_or_default(self.args['iterations'], 40000)
            self.args['steps'] = none_or_default(self.args['steps'], [20000])
            # self.args['single_object'] = True

    def __getitem__(self, key):
        return self.args[key]

    def __setitem__(self, key, value):
        self.args[key] = value

    def __str__(self):
        return str(self.args)
