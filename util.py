import numpy as np
import six


def reduce_cluster_graph(netlists, clusters, fixed_blocks,
                         cluster_id=None):
    """NOTE: cluster_blocks holds block IDs, not cell locations"""
    if cluster_id is None:
        cluster_id = 0
        condense_self = True
    else:
        condense_self = False
    current_cluster = clusters[cluster_id]
    new_netlist = {}
    for net_id in netlists:
        netlist = []
        for blk_id in netlists[net_id]:
            if blk_id not in netlist:
                netlist.append(blk_id)
        netlist_set = set(netlist)
        if len(netlist_set.intersection(current_cluster)) > 0:
            # we need to reduce the net
            new_net = []
            for blk_id in netlist:
                if blk_id in current_cluster:
                    if condense_self:
                        new_node = "x" + str(cluster_id)
                        new_net.append(new_node)
                    else:
                        new_net.append(blk_id)
                elif blk_id in fixed_blocks:
                    new_net.append(blk_id)
                else:
                    # search for all the other netlists
                    # we use "x" for clusters
                    found = False
                    for cid in clusters:
                        if cid == cluster_id:
                            continue
                        if blk_id in clusters[cid]:
                            new_node = "x" + str(cid)
                            new_net.append(new_node)
                            found = True
                            break
                    if not found:
                        raise Exception("not found blk", blk_id)

            new_netlist[net_id] = new_net
    return new_netlist


def compute_centroid(cluster_cells):
    if type(cluster_cells) == list or type(cluster_cells) == set:
        x_sum = 0
        y_sum = 0
        cluster_size = len(cluster_cells)
        for cell in cluster_cells:
            x_sum += cell[0]
            y_sum += cell[1]
        pos_x = int(x_sum / cluster_size)
        pos_y = int(y_sum / cluster_size)
        return pos_x, pos_y
    elif type(cluster_cells) == dict:
        x_sum = 0
        y_sum = 0
        cluster_size = len(cluster_cells)
        for cell_id in cluster_cells:
            cell = cluster_cells[cell_id]
            x_sum += cell[0]
            y_sum += cell[0]
        pos_x = int(x_sum / cluster_size)
        pos_y = int(y_sum / cluster_size)
        return pos_x, pos_y
    else:
        raise Exception("Unknown type: " + str(type(cluster_cells)))


def compute_centroids(cluster_cells, b_type):
    result = {}
    for cluster_id in cluster_cells:
        cells = set()
        for blk_type in cluster_cells[cluster_id]:
            if b_type != blk_type:
                continue
            cells.update(cluster_cells[cluster_id][blk_type])
        pos = compute_centroid(cells)
        result[cluster_id] = pos
    return result
