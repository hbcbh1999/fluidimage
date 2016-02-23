
from __future__ import print_function

import numpy as np

from time import clock


from fluidimage.calcul.correl import (
    CorrelScipySignal, CorrelScipyNdimage, CorrelFFTNumpy, CorrelFFTW)

from fluidimage.synthetic import make_synthetic_images

nx = 32
ny = 32
displacement_x = 2.
displacement_y = 2.

displacements = np.array([displacement_y, displacement_x])

nb_particles = (nx // 3)**2


print('nx: {} ; ny: {}'.format(nx, ny))

im0, im1 = make_synthetic_images(
    displacements, nb_particles, shape_im0=(ny, nx), epsilon=0.)


classes = {'sig': CorrelScipySignal, 'ndimage': CorrelScipyNdimage,
           'np.fft': CorrelFFTNumpy, 'fftw': CorrelFFTW}


cs = {}
funcs = {}
for k, cls in classes.items():
    calcul_corr = cls(im0.shape, im1.shape)
    funcs[k] = calcul_corr
    t = clock()
    cs[k] = calcul_corr(im0, im1)
    print('calcul correl with {} : {} s'.format(k, clock() - t))