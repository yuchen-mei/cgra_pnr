from __future__ import print_function
from util import reduce_cluster_graph
from argparse import ArgumentParser
from arch.parser import parse_emb
from sa import SAClusterPlacer, SADetailedPlacer
from sa import ClusterException
from arch import make_board, parse_cgra, generate_place_on_board
from arch import generate_is_cell_legal
import numpy as np
import os
import pickle
from visualize import visualize_placement_cgra
from sklearn.cluster import KMeans
import random
from multiprocessing import Pool
import multiprocessing
from arch.cgra import place_special_blocks, save_placement, prune_netlist
from arch.cgra_packer import load_packed_file


def detailed_placement(args, context=None):
    if context is not None:
        args = pickle.loads(args["body"])
    clusters = args["clusters"]
    cells = args["cells"]
    netlist = args["new_netlist"]
    raw_netlist = args["raw_netlist"]
    board = args["board"]
    blk_pos = args["blk_pos"]
    fold_reg = args["fold_reg"]
    seed = args["seed"]
    fallback = args["fallback"]
    detailed = SADetailedPlacer(clusters, cells, netlist, raw_netlist,
                                board, blk_pos,
                                fold_reg=fold_reg, seed=seed)
    if fallback:
        detailed.steps *= 5
    # detailed.steps = 10
    detailed.anneal()
    if context is None:
        return detailed.state
    else:
        return {'statusCode': 200,
                'headers': {'Content-Type': 'application/json'},
                'body': detailed.state
                }


def main():
    parser = ArgumentParser("CGRA Placer")
    parser.add_argument("-i", "--input", help="Packed netlist file, " +
                                              "e.g. harris.packed",
                        required=True, action="store", dest="packed_filename")
    parser.add_argument("-e", "--embedding", help="Netlist embedding file, " +
                        "e.g. harris.emb",
                        required=True, action="store", dest="netlist_embedding")
    parser.add_argument("-o", "--output", help="Placement result, " +
                                               "e.g. harris.place",
                        required=True, action="store",
                        dest="placement_filename")
    parser.add_argument("-c", "--cgra", help="CGRA architecture file",
                        required=True, action="store", dest="arch_filename")
    parser.add_argument("--no-reg-fold", help="If set, the placer will treat " +
                                              "registers as PE tiles",
                        action="store_true",
                        required=False, dest="no_reg_fold", default=False)
    parser.add_argument("--no-vis", help="If set, the placer won't show " +
                        "visualization result for placement",
                        action="store_true",
                        required=False, dest="no_vis", default=False)
    parser.add_argument("-s", "--seed", help="Seed for placement. " +
                        "default is 0", type=int, default=0,
                        required=False, action="store", dest="seed")

    parser.add_argument("-u", "--url", help="Lambda function entry for " +
                        "detailed placement. If set, will try to connect to "
                        "that URL",
                        dest="lambda_url", type=str, required=False,
                        action="store", default="")

    args = parser.parse_args()

    arch_filename = args.arch_filename
    packed_filename = args.packed_filename
    netlist_embedding = args.netlist_embedding
    placement_filename = args.placement_filename
    lambda_url = args.lambda_url

    seed = args.seed
    print("Using seed", seed, "for placement")
    # just in case for some library
    random.seed(seed)
    np.random.seed(seed)

    vis_opt = not args.no_vis
    fold_reg = not args.no_reg_fold

    board_meta = parse_cgra(arch_filename, fold_reg=fold_reg)
    board_name, board_meta = board_meta.popitem()
    print("INFO: Placing for", board_name)
    num_dim, raw_emb = parse_emb(netlist_embedding)
    board = make_board(board_meta)
    place_on_board = generate_place_on_board(board_meta, fold_reg=fold_reg)
    is_cell_legal = generate_is_cell_legal(board_meta, fold_reg=fold_reg)

    fixed_blk_pos = {}
    emb = {}
    raw_netlist, folded_blocks, id_to_name, changed_pe = \
        load_packed_file(packed_filename)
    netlists = prune_netlist(raw_netlist)
    special_blocks = set()
    for blk_id in raw_emb:
        if blk_id[0] == "i":
            special_blocks.add(blk_id)
        else:
            emb[blk_id] = raw_emb[blk_id]
    # place the spacial blocks first
    place_special_blocks(board, special_blocks, fixed_blk_pos, raw_netlist,
                         id_to_name,
                         place_on_board,
                         board_meta)

    data_x = np.zeros((len(emb), num_dim))
    blks = list(emb.keys())
    for i in range(len(blks)):
        data_x[i] = emb[blks[i]]

    num_of_kernels = get_num_clusters(id_to_name)

    centroids, cluster_cells, clusters, fallback = perform_global_placement(
        blks, data_x, emb, fixed_blk_pos, netlists, board, is_cell_legal,
        board_meta, fold_reg=fold_reg, num_clusters=num_of_kernels,
        seed=seed)

    # anneal with each cluster
    board_pos = perform_detailed_placement(board, centroids,
                                           cluster_cells, clusters,
                                           fixed_blk_pos, netlists,
                                           raw_netlist,
                                           fold_reg, seed, fallback,
                                           lambda_url)

    # do a macro placement
    # macro_result = macro_placement(board, board_pos, fixed_blk_pos, netlists,
    #                               is_cell_legal, board_meta)
    # board_pos.update(macro_result)


    for blk_id in board_pos:
        pos = board_pos[blk_id]
        place_on_board(board, blk_id, pos)

    # save the placement file
    save_placement(board_pos, id_to_name, folded_blocks, placement_filename)
    basename_file = os.path.basename(placement_filename)
    design_name, _ = os.path.splitext(basename_file)
    if vis_opt:
        visualize_placement_cgra(board_meta, board_pos, design_name, changed_pe)


def get_num_clusters(id_to_name):
    unique_names = set()
    for blk_id in id_to_name:
        blk_name = id_to_name[blk_id]
        name = blk_name.split(".")[0]
        name = name.split("$")[0]
        unique_names.add(name)

    count = [1 for name in unique_names if name[:2] == "lb" and
             "lut" not in name]
    return sum(count)


def perform_global_placement(blks, data_x, emb, fixed_blk_pos, netlists, board,
                             is_cell_legal, board_meta, fold_reg, seed,
                             num_clusters=None):
    # simple heuristics to calculate the clusters
    if num_clusters is None or num_clusters == 0:
        num_clusters = int(np.ceil(len(emb) / 40)) + 1
    # extra careful
    num_clusters = min(num_clusters, len(blks))
    factor = 6
    clusters = {}
    while True:     # this just enforce we can actually place it
        if num_clusters == 0:
            cluster_placer = None
            break
        print("Trying: num of clusters", num_clusters)
        kmeans = KMeans(n_clusters=num_clusters, random_state=0).fit(data_x)
        cluster_ids = kmeans.labels_
        clusters = {}
        for i in range(len(blks)):
            cid = cluster_ids[i]
            if cid not in clusters:
                clusters[cid] = {blks[i]}
            else:
                clusters[cid].add(blks[i])
        cluster_sizes = [len(clusters[s]) for s in clusters]
        print("cluster average:", np.average(cluster_sizes), "std:",
              np.std(cluster_sizes), "total:", np.sum(cluster_sizes))
        try:
            cluster_placer = SAClusterPlacer(clusters, netlists, board,
                                             fixed_blk_pos,
                                             board_meta=board_meta,
                                             fold_reg=fold_reg,
                                             seed=seed)
            break
        except ClusterException as _:
            num_clusters -= 1
            factor = 4
    if num_clusters > 0:
        # cluster_placer.steps = 200
        cluster_placer.anneal()
        cluster_cells, centroids = cluster_placer.squeeze()
        fallback = False
    else:
        # it exceeds the algorithm's limit
        # fall back to full-size annealing
        print("Netlist too big. Fall back to full-board annealing")
        clusters = {0: blks}
        # put there one by one
        cluster_cells = {0: {}}
        available_cells = []
        board_layout = board_meta[0]
        mem_cells = set()
        for y in range(len(board_layout)):
            for x in range(len(board_layout[y])):
                if board_layout[y][x] == "p":
                    available_cells.append((x, y))
                elif board_layout[y][x] == "m":
                    mem_cells.add((x, y))
        p_blks = [b for b in blks if b[0] == "p"]
        m_blks = [b for b in blks if b[0] == "m"]
        if len(available_cells) < len(p_blks):
            raise Exception("We have " + str(len(available_cells)) +
                            " PE tiles, but the netlist needs " +
                            str(len(p_blks)))
        else:
            print("Using", len(p_blks), "out of", len(available_cells),
                  "available PE tiles")
        cluster_cells[0]["p"] = set(available_cells[:len(p_blks)])
        # handle memory
        if len(mem_cells) < len(m_blks):
            raise Exception("We have " + str(len(mem_cells)) +
                            " MEM tiles, but the netlist needs " +
                            str(len(m_blks)))
        else:
            print("Using", len(m_blks), "out of", len(mem_cells),
                  "available MEM tiles")
        cluster_cells[0]["m"] = mem_cells
        centroids = {0: (len(board_layout[0]) // 2, len(board_layout) // 2)}
        fallback = True

    return centroids, cluster_cells, clusters, fallback


def perform_detailed_placement(board, centroids, cluster_cells, clusters,
                               fixed_blk_pos, netlists, raw_netlist,
                               fold_reg, seed, fallback, lambda_url=""):
    board_pos = fixed_blk_pos.copy()
    map_args = []
    for c_id in cluster_cells:
        cells = cluster_cells[c_id]
        new_netlist = reduce_cluster_graph(netlists, clusters,
                                           fixed_blk_pos, c_id)
        blk_pos = fixed_blk_pos.copy()
        for i in centroids:
            if i == c_id:
                continue
            node_id = "x" + str(i)
            pos = centroids[i]
            blk_pos[node_id] = pos
        args = {"clusters": clusters[c_id], "cells": cells,
                "new_netlist": new_netlist, "raw_netlist": raw_netlist,
                "board": board, "blk_pos": blk_pos, "fold_reg": fold_reg,
                "seed": seed, "fallback": fallback}

        map_args.append(args)
    if lambda_url:
        from requests_futures.sessions import FuturesSession
        session = FuturesSession()
        res_list = []
        import time
        for arg in map_args:
            print("time:", time.time())
            r = session.post(lambda_url, data=pickle.dumps(arg))
            res_list.append(r)
        # merge
        for res in res_list:
            r = res.json()
            board_pos.update(r)
    else:
        num_of_cpus = min(multiprocessing.cpu_count(), len(clusters))
        pool = Pool(num_of_cpus)
        # detailed_placement(map_args[0])
        results = pool.map(detailed_placement, map_args)
        pool.close()
        pool.join()
        for r in results:
            board_pos.update(r)

    return board_pos


if __name__ == "__main__":
    main()
