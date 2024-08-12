import os
import json
import numpy as np
import copy
import torch
from itertools import product
import torch.nn.functional as F

if __name__ == '__main__':
    os.sys.path.append('./src')
from src.utils.config import Config
from src.model.model import MMGNet
from src.utils import op_utils


class EdgePredictor:
    def __init__(self, config_path, ckpt_path, relationships_list):
        self.config = Config(config_path)
        self.config.exp = ckpt_path
        self.config.MODE = "eval"
        self.padding = 0.2
        self.model = MMGNet(self.config)
        # init device
        if torch.cuda.is_available() and len(self.config.GPU) > 0:
            self.config.DEVICE = torch.device("cuda")
        else:
            self.config.DEVICE = torch.device("cpu")
        self.model.load(best=True)
        with open(relationships_list, "r") as f:
            self.relationships_list = f.readlines()
        
        self.rel_id_to_rel_name = {
            i: name.strip()
            for i, name in enumerate(self.relationships_list[1:])
        }

    def preprocess_poinclouds(self, points, num_points):
        assert len(points) > 1, "Number of objects should be at least 2"
        edge_indices = list(product(list(range(len(points))), list(range(len(points)))))
        edge_indices = [i for i in edge_indices if i[0]!=i[1]]

        num_objects = len(points)
        dim_point = points[0].shape[-1]

        instances_box = dict()
        obj_points = torch.zeros([num_objects, num_points, dim_point])
        descriptor = torch.zeros([num_objects, 11])

        obj_2d_feats = np.zeros([num_objects, 512])

        for i, pcd in enumerate(points):
            # get node point
            min_box = np.min(pcd, 0) - self.padding
            max_box = np.max(pcd, 0) + self.padding
            instances_box[i] = (min_box, max_box)
            choice = np.random.choice(len(pcd), num_points, replace=True)
            pcd = pcd[choice, :]
            descriptor[i] = op_utils.gen_descriptor(torch.from_numpy(pcd))
            pcd = torch.from_numpy(pcd.astype(np.float32))
            pcd = self.zero_mean(pcd)
            obj_points[i] = pcd

        edge_indices = torch.tensor(edge_indices, dtype=torch.long)
        obj_2d_feats = torch.from_numpy(obj_2d_feats.astype(np.float32))    
        obj_points = obj_points.permute(0, 2, 1)
        batch_ids = torch.zeros((num_objects, 1))
        return obj_points, obj_2d_feats, edge_indices, descriptor, batch_ids

    def predict_relations(self, obj_points, obj_2d_feats, edge_indices, descriptor, batch_ids):
        obj_points = obj_points.to(self.config.DEVICE)
        obj_2d_feats = obj_2d_feats.to(self.config.DEVICE)
        edge_indices = edge_indices.to(self.config.DEVICE)
        descriptor = descriptor.to(self.config.DEVICE)
        batch_ids = batch_ids.to(self.config.DEVICE)
        self.model.model.eval()
        with torch.no_grad():
            rel_cls_3d = self.model.model(
                obj_points, obj_2d_feats, edge_indices, descriptor, batch_ids=batch_ids
            )
        return rel_cls_3d

    def save_relations(self, tracking_ids, timestamps, class_names, predicted_relations, edge_indices):
        saved_relations = []
        for k in range(predicted_relations.shape[0]):
            idx_1 = edge_indices[k][0].item()
            idx_2 = edge_indices[k][1].item()

            id_1 = tracking_ids[idx_1]
            id_2 = tracking_ids[idx_2]

            timestamp_1 = timestamps[idx_1]
            timestamp_2 = timestamps[idx_2]

            class_name_1 = class_names[idx_1]
            class_name_2 = class_names[idx_2]

            rel_id = torch.argmax(predicted_relations, dim=1)[k].item()
            rel_name = self.rel_id_to_rel_name[rel_id]

            rel_dict = {
                "id_1": id_1,
                "timestamp_1": timestamp_1,
                "class_name_1": class_name_1,
                "id_2": id_2,
                "timestamp_2": timestamp_2,
                "class_name_2": class_name_2,
                "rel_id": rel_id,
                "rel_name": rel_name
            }
            saved_relations.append(rel_dict)

        return saved_relations

    def zero_mean(self, point):
        mean = torch.mean(point, dim=0)
        point -= mean.unsqueeze(0)
        return point


def main():
    config_path = "config/mmgnet.json"
    ckpt_path = "/hdd/wingrune/3dssg_best_ckpt"
    data_path = "./point_clouds-2"
    relationships_list = "/home/wingrune/CVPR2023-VLSAT/data/3DSSG_subset/relationships.txt"

    tracking_ids = ['786', '1024']
    timestamps = ["001539", '001539']
    class_names = ["orange box", "green box"]

    pcds = {}
    dirname = "point_clouds-2"
    framename = "frame_336.json"
    with open(os.path.join(dirname, framename)) as f:
        frame = json.load(f)
    
    for i, track_id in enumerate(tracking_ids):
        pcds[track_id] = {}
        pcds[track_id][timestamps[i]] = {}
        for obj in frame["tracked_objects"]:
            if obj["tracking_id"] == int(track_id):
                pcds[track_id][timestamps[i]]['point_cloud'] = copy.deepcopy(np.array(obj['point_cloud']))
                pcds[track_id][timestamps[i]]['point_cloud'] = pcds[track_id][timestamps[i]]['point_cloud'][:, [2, 0, 1]]
                pcds[track_id][timestamps[i]]['position'] = [
                    np.round(np.mean(pcds[track_id][timestamps[i]]['point_cloud'][:, 0]),2),
                    np.round(np.mean(pcds[track_id][timestamps[i]]['point_cloud'][:, 1]),2),
                    np.round(np.mean(pcds[track_id][timestamps[i]]['point_cloud'][:, 2]),2),
                ]

    print("Loaded the following saved pointclouds:")
    for obj_id, obj_pcds in pcds.items():
        for timecode in obj_pcds:
            print(obj_id, "at time", timecode, "with position ", obj_pcds[timecode]['position'], "point cloud shape", obj_pcds[timecode]['point_cloud'].shape)
    #exit()
    edge_predictor = EdgePredictor(config_path, ckpt_path, relationships_list)

    obj_points, obj_2d_feats, edge_indices, descriptor, batch_ids = edge_predictor.preprocess_poinclouds(
        [
            pcds['786']["001539"]['point_cloud'],
            pcds['1024']["001539"]['point_cloud']
        ],
        edge_predictor.config.dataset.num_points
    )
    predicted_relations = edge_predictor.predict_relations(obj_points, obj_2d_feats, edge_indices, descriptor, batch_ids)
    #print(predicted_relations.shape)
    #topk_values, topk_indices = torch.topk(predicted_relations, 5, dim=1,  largest=True)
    #print(topk_indices, topk_values)
    saved_relations = edge_predictor.save_relations(tracking_ids, timestamps, class_names, predicted_relations, edge_indices)

    print("Predicted the following relations:")
    print(saved_relations)
    return saved_relations


if __name__ == "__main__":
    main() 
