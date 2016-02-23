
import pstats
import cProfile

from fluiddyn.util.serieofarrays import SeriesOfArrays
from fluidimage.works.piv import WorkPIV

params = WorkPIV.create_default_params()

piv = WorkPIV(params=params)

series = SeriesOfArrays('../image_samples/Oseen/Images', 'i+1:i+3')
serie = series.get_serie_from_index(0)


cProfile.runctx('result = piv.calcul(serie)',
                globals(), locals(), 'profile.pstats')

s = pstats.Stats('profile.pstats')
s.strip_dirs().sort_stats('time').print_stats(10)

# with gprof2dot and graphviz (command dot):
# gprof2dot -f pstats profile.pstats | dot -Tpng -o profile.png
