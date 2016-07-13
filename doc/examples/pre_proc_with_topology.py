from __future__ import print_function

from fluidimage import config_logging
from fluidimage.topologies.pre_proc import TopologyPreproc


config_logging('info')

params = TopologyPreproc.create_default_params()

params.preproc.series.path = '../../image_samples/Karman/Images'
params.preproc.series.strcouple = 'i:i+3'
params.preproc.series.ind_start = 1

print('Available preprocessing tools: ', params.preproc.tools.available_tools)
params.preproc.tools.sequence = ['temporal_median', 'sliding_median', 'global_threshold']
print('Enabled preprocessing tools: ', params.preproc.tools.sequence)

params.preproc.tools.sliding_median.enable = True
params.preproc.tools.sliding_median.weight = 0.5
params.preproc.tools.sliding_median.window_size = 10

params.preproc.tools.temporal_median.enable = True
params.preproc.tools.temporal_median.weight = 0.5
params.preproc.tools.temporal_median.window_shape = (3, 2, 2)

params.preproc.tools.global_threshold.enable = True
params.preproc.tools.global_threshold.minima = 0.

topology = TopologyPreproc(params)

topology.compute(sequential=False)