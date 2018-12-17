import argparse
import os
import json
import numpy as np
import open3d
import time

from dataset.kitti_dataset import KittiDataset
from predict import Predictor


def interpolate_dense_labels(sparse_points, sparse_labels, dense_points, k=20):
    sparse_pcd = open3d.PointCloud()
    sparse_pcd.points = open3d.Vector3dVector(sparse_points)
    sparse_pcd_tree = open3d.KDTreeFlann(sparse_pcd)

    dense_labels = []
    for dense_point in dense_points:
        result_k, sparse_indexes, _ = sparse_pcd_tree.search_knn_vector_3d(
            dense_point, k
        )
        knn_sparse_labels = sparse_labels[sparse_indexes]
        dense_label = np.bincount(knn_sparse_labels).argmax()
        dense_labels.append(dense_label)
    return dense_labels


if __name__ == "__main__":
    np.random.seed(0)

    # Parser
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--num_samples",
        type=int,
        default=8,
        help="# samples, each contains num_point points",
    )
    parser.add_argument("--ckpt", default="", help="Checkpoint file")
    flags = parser.parse_args()
    hyper_params = json.loads(open("semantic_no_color.json").read())

    # Create output dir
    sparse_output_dir = os.path.join("result", "sparse")
    dense_output_dir = os.path.join("result", "dense")
    os.makedirs(sparse_output_dir, exist_ok=True)
    os.makedirs(dense_output_dir, exist_ok=True)

    # Dataset
    dataset = KittiDataset(
        num_points_per_sample=hyper_params["num_point"],
        base_dir="/home/ylao/data/kitti",
        dates=["2011_09_26"],
        # drives=["0095", "0001"],
        drives=["0095"],
        box_size=hyper_params["box_size"],
    )

    # Model
    batch_size = 128  # The more the better, limited by memory size
    predictor = Predictor(
        checkpoint_path=flags.ckpt,
        num_classes=dataset.num_classes,
        hyper_params=hyper_params,
    )

    for kitti_file_data in dataset.list_file_data[:5]:
        timer = {"load_data": 0, "predict": 0, "interpolate": 0, "write_data": 0}

        # Predict for num_samples times
        points_raw_collector = []
        pd_labels_collector = []

        # If flags.num_samples < batch_size, will predict one batch
        for batch_index in range(int(np.ceil(flags.num_samples / batch_size))):
            current_batch_size = min(
                batch_size, flags.num_samples - batch_index * batch_size
            )

            # Get data
            start_time = time.time()
            points, points_raw, gt_labels, colors = kitti_file_data.sample_batch(
                batch_size=current_batch_size,
                num_points_per_sample=hyper_params["num_point"],
            )
            # (bs, 8192, 3) concat (bs, 8192, 3) -> (bs, 8192, 6)
            if hyper_params["use_color"]:
                points_with_colors = np.concatenate((points, colors), axis=-1)
            else:
                points_with_colors = points
            timer["load_data"] += time.time() - start_time

            # Predict
            start_time = time.time()
            pd_labels = predictor.predict(points_with_colors)
            points_raw_collector.extend(points_raw)
            pd_labels_collector.extend(pd_labels)
            timer["predict"] += time.time() - start_time

        points_raw_collector = np.array(points_raw_collector)
        pd_labels_collector = np.array(pd_labels_collector).astype(int)

        # Interpolate to original point cloud
        start_time = time.time()
        dense_points = kitti_file_data.points + kitti_file_data.points_min_raw
        dense_labels = interpolate_dense_labels(
            sparse_points=points_raw_collector.reshape((-1, 3)),
            sparse_labels=pd_labels_collector.flatten(),
            dense_points=dense_points.reshape((-1, 3)),
        )
        timer["interpolate"] += time.time() - start_time

        start_time = time.time()
        # Save sparse point cloud with predicted labels
        file_prefix = os.path.basename(kitti_file_data.file_path_without_ext)

        pcd = open3d.PointCloud()
        pcd.points = open3d.Vector3dVector(points_raw_collector.reshape((-1, 3)))
        pcd_path = os.path.join(sparse_output_dir, file_prefix + ".pcd")
        open3d.write_point_cloud(pcd_path, pcd)
        print("Exported pcd to {}".format(pcd_path))

        pd_labels_path = os.path.join(sparse_output_dir, file_prefix + ".labels")
        np.savetxt(pd_labels_path, pd_labels_collector.flatten(), fmt="%d")
        print("Exported labels to {}".format(pd_labels_path))

        # Save dense point cloud with predicted labels
        dense_pcd = open3d.PointCloud()
        dense_pcd.points = open3d.Vector3dVector(dense_points.reshape((-1, 3)))
        dense_pcd_path = os.path.join(dense_output_dir, file_prefix + ".pcd")
        open3d.write_point_cloud(dense_pcd_path, dense_pcd)
        print("Exported dense_pcd to {}".format(dense_pcd_path))

        dense_labels_path = os.path.join(dense_output_dir, file_prefix + ".labels")
        np.savetxt(dense_labels_path, dense_labels, fmt="%d")
        print("Exported dense_labels to {}".format(dense_labels_path))
        timer["write_data"] += time.time() - start_time

        print(timer)