import os.path as osp
import os
os.environ['CUDA_LAUNCH_BLOCKING'] = "1"
import sys
sys.path.insert(0, os.path.abspath('../'))

from common.utils import load_data, get_device, freeze_seed
from attackers.mettack import MetaApprox, Metattack


from deeprobust.graph.defense import GCN
from deeprobust.graph.utils import *
import argparse

from torch_sparse import SparseTensor



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gpu_id', type=int, default=3)
    parser.add_argument('--seed', type=int, default=15)
    parser.add_argument('--dataset', type=str, default='pubmed',
                        choices=['cora', 'citeseer', 'pubmed', 'cora_ml'])
    parser.add_argument('--ptb_rate', type=float, default=0.05)
    parser.add_argument('--attack_type', type=str, default='Meta-Self',
                        choices=['Meta-Self', 'A-Meta-Self', 'Meta-Train', 'A-Meta-Train'])
    parser.add_argument('--save', type=bool, default=True)

    args = parser.parse_args()
    assert args.gpu_id in range(0, 4)

    device = get_device(args.gpu_id)
    freeze_seed(args.seed)

    hidden = 16
    dropout = 0.5
    attack_type = args.attack_type

    pyg_data = load_data(name=args.dataset, x_normalize=False)
    adj, features, labels = pyg_data.adj_t.to_scipy(layout='csr'), pyg_data.x, pyg_data.y
    idx_train = torch.nonzero(pyg_data.train_mask).flatten()
    idx_val = torch.nonzero(pyg_data.val_mask).flatten()
    idx_test = torch.nonzero(pyg_data.test_mask).flatten()
    idx_unlabeled = np.union1d(idx_val, idx_test)

    perturbations = int(args.ptb_rate * (adj.sum() // 2))
    adj, features, labels = preprocess(adj, features, labels, preprocess_adj=False)

    def _test(adj):
        # adj = normalize_adj_tensor(adj)
        gcn = GCN(nfeat=pyg_data.num_features,
                  nhid=hidden,
                  nclass=pyg_data.num_classes,
                  dropout=dropout, device=device)
        gcn = gcn.to(device)
        gcn.fit(features, adj, labels, idx_train)  # train without model picking
        # gcn.fit(features, adj, labels, idx_train, idx_val) # train with validation model picking
        output = gcn.output.cpu()
        loss_test = F.nll_loss(output[idx_test], labels[idx_test])
        acc_test = accuracy(output[idx_test], labels[idx_test])
        print("Test set results:",
              "loss= {:.4f}".format(loss_test.item()),
              "accuracy= {:.4f}".format(acc_test.item()))

        return acc_test.item()

    lambda_ = 0
    # Setup Attack Model
    if 'Self' in attack_type:
        lambda_ = 0
    if 'Train' in attack_type:
        lambda_ = 1
    if 'Both' in attack_type:
        lambda_ = 0.5

    MetaFunc = Metattack
    if 'A' in attack_type:
        MetaFunc = MetaApprox

    modified_adj_list = []

    for i in range(5):
        freeze_seed(args.seed + i)
        # Setup Surrogate Model
        surrogate = GCN(
            nfeat=pyg_data.num_features, nclass=pyg_data.num_classes,
            nhid=16, dropout=0.5, with_relu=False, with_bias=True, weight_decay=5e-4,
            device=device)

        surrogate = surrogate.to(device)
        surrogate.fit(features, adj, labels, idx_train)

        model = MetaFunc(
            model=surrogate, nnodes=adj.shape[0],
            feature_shape=features.shape,
            attack_structure=True, attack_features=False,
            device=device, lambda_=lambda_)
        model = model.to(device)

        model.attack(features, adj, labels, idx_train, idx_unlabeled, perturbations, ll_constraint=False)
        print('=== testing GCN on original(clean) graph ===')
        _test(adj)
        modified_adj = model.modified_adj
        # modified_features = model.modified_features
        _test(modified_adj)
        # # if you want to save the modified adj/features, uncomment the code below
        # model.save_adj(root='./', name=f'mod_adj')
        # model.save_features(root='./', name='mod_features')
        modified_adj_list.append(SparseTensor.from_dense(modified_adj.cpu()))

    if args.save:
        save_path = "./perturbed_adjs/"
        if not osp.exists(save_path):
            os.makedirs(save_path)
        save_path += f"mettack-{args.dataset}-{args.ptb_rate}.pth"
        torch.save(obj={
            'modified_adj_list': modified_adj_list,
        }, f=save_path)



if __name__ == '__main__':
    main()
