from math import sqrt
import numpy as np
import numba as nb

from numba import cuda

from PIL import Image

from xarray import DataArray
import dask.array as da

from xrspatial.utils import cuda_args
from xrspatial.utils import ngjit
from xrspatial.utils import ArrayTypeFunctionMapping
from xrspatial.utils import validate_arrays


# 3rd-party
try:
    import cupy
except ImportError:
    class cupy(object):
        ndarray = False


@ngjit
def _avri_cpu(nir_data, red_data, blue_data):
    out = np.zeros_like(nir_data, dtype='f4')
    rows, cols = nir_data.shape
    for y in range(0, rows):
        for x in range(0, cols):
            nir = nir_data[y, x]
            red = red_data[y, x]
            blue = blue_data[y, x]
            numerator = (nir - (2.0 * red) + blue)
            denominator = (nir + (2.0 * red) + blue)
            out[y, x] = numerator / denominator

    return out


@cuda.jit
def _arvi_gpu(nir_data, red_data, blue_data, out):
    y, x = cuda.grid(2)
    if y < out.shape[0] and x < out.shape[1]:
        nir = nir_data[y, x]
        red = red_data[y, x]
        blue = blue_data[y, x]
        numerator = (nir - (2.0 * red) + blue)
        denominator = (nir + (2.0 * red) + blue)
        out[y, x] = numerator / denominator


def _arvi_dask(nir_data, red_data, blue_data):
    out = da.map_blocks(_avri_cpu, nir_data, red_data, blue_data,
                        meta=np.array(()))
    return out


def _arvi_cupy(nir_data, red_data, blue_data):

    import cupy

    griddim, blockdim = cuda_args(nir_data.shape)
    out = cupy.empty(nir_data.shape, dtype='f4')
    out[:] = cupy.nan
    _arvi_gpu[griddim, blockdim](nir_data, red_data, blue_data, out)
    return out


def _arvi_dask_cupy(nir_data, red_data, blue_data):

    import cupy

    out = da.map_blocks(_arvi_cupy, nir_data, red_data, blue_data,
                        dtype=cupy.float32, meta=cupy.array(()))
    return out


def arvi(nir_agg: DataArray, red_agg: DataArray, blue_agg: DataArray,
         name='arvi'):
    """Computes Atmospherically Resistant Vegetation Index

    Parameters
    ----------
    nir_agg : DataArray
        near-infrared band data

    red_agg : DataArray
        red band data

    blue_agg : DataArray
        blue band data

    Returns
    -------
    data: DataArray

    Notes:
    ------
    Algorithm References:
    https://modis.gsfc.nasa.gov/sci_team/pubs/abstract_new.php?id=03667
    """
    validate_arrays(red_agg, nir_agg, blue_agg)

    mapper = ArrayTypeFunctionMapping(numpy_func=_avri_cpu,
                                      dask_func=_arvi_dask,
                                      cupy_func=_arvi_cupy,
                                      dask_cupy_func=_arvi_dask_cupy)

    out = mapper(red_agg)(nir_agg.data, red_agg.data, blue_agg.data)

    return DataArray(out,
                     name=name,
                     coords=nir_agg.coords,
                     dims=nir_agg.dims,
                     attrs=nir_agg.attrs)


# EVI -------------
@ngjit
def _evi_cpu(nir_data, red_data, blue_data, c1, c2, soil_factor, gain):
    out = np.zeros_like(nir_data, dtype='f4')
    rows, cols = nir_data.shape
    for y in range(0, rows):
        for x in range(0, cols):
            nir = nir_data[y, x]
            red = red_data[y, x]
            blue = blue_data[y, x]
            numerator = nir - red
            denominator = nir + c1 * red - c2 * blue + soil_factor
            out[y, x] = gain * (numerator / denominator)
    return out


@cuda.jit
def _evi_gpu(nir_data, red_data, blue_data, c1, c2, soil_factor, gain, out):
    y, x = cuda.grid(2)
    if y < out.shape[0] and x < out.shape[1]:
        nir = nir_data[y, x]
        red = red_data[y, x]
        blue = blue_data[y, x]
        numerator = nir - red
        denominator = nir + c1 * red - c2 * blue + soil_factor
        out[y, x] = gain * (numerator / denominator)


def _evi_dask(nir_data, red_data, blue_data, c1, c2, soil_factor, gain):
    out = da.map_blocks(_evi_cpu, nir_data, red_data, blue_data,
                        c1, c2, soil_factor, gain, meta=np.array(()))
    return out


def _evi_cupy(nir_data, red_data, blue_data, c1, c2, soil_factor, gain):

    import cupy

    griddim, blockdim = cuda_args(nir_data.shape)
    out = cupy.empty(nir_data.shape, dtype='f4')
    out[:] = cupy.nan
    args = (nir_data, red_data, blue_data, c1, c2, soil_factor, gain, out)
    _evi_gpu[griddim, blockdim](*args)
    return out


def _evi_dask_cupy(nir_data, red_data, blue_data, c1, c2, soil_factor, gain):

    import cupy

    out = da.map_blocks(_evi_cupy, nir_data, red_data, blue_data,
                        c1, c2, soil_factor, gain,
                        dtype=cupy.float32, meta=cupy.array(()))
    return out


def evi(nir_agg: DataArray, red_agg: DataArray, blue_agg: DataArray,
        c1=6.0, c2=7.5, soil_factor=1.0, gain=2.5, name='evi'):
    """Computes Enhanced Vegetation Index

    Parameters
    ----------
    nir_agg : DataArray
        near-infrared band data

    red_agg : DataArray
        red band data

    blue_agg : DataArray
        blue band data

    c1 : float (default=6.0)
        first coefficient of the aerosol resistance term

    c2 : float (default=7.5)
        second coefficients of the aerosol resistance term

    soil_factor : float (default=1.0)
      soil adjustment factor [-1.0, 1.0] used to adjust canopy background

    gain : float (default=2.5)
      amplitude adjustment factor

    Returns
    -------
    data: DataArray

    Notes:
    ------
    Algorithm References:
    https://en.wikipedia.org/wiki/Enhanced_vegetation_index
    """

    if not red_agg.shape == nir_agg.shape == blue_agg.shape:
        raise ValueError("input layers expected to have equal shapes")

    if not isinstance(c1, (float, int)):
        raise ValueError("c1 must be numeric")

    if not isinstance(c2, (float, int)):
        raise ValueError("c2 must be numeric")

    if soil_factor > 1.0 or soil_factor < -1.0:
        raise ValueError("soil factor must be between [-1.0, 1.0]")

    if gain < 0:
        raise ValueError("gain must be greater than 0")

    validate_arrays(nir_agg, red_agg, blue_agg)

    mapper = ArrayTypeFunctionMapping(numpy_func=_evi_cpu,
                                      dask_func=_evi_dask,
                                      cupy_func=_evi_cupy,
                                      dask_cupy_func=_evi_dask_cupy)

    out = mapper(red_agg)(nir_agg.data, red_agg.data, blue_agg.data, c1, c2,
                          soil_factor, gain)

    return DataArray(out,
                     name=name,
                     coords=nir_agg.coords,
                     dims=nir_agg.dims,
                     attrs=nir_agg.attrs)


# GCI -------------
@ngjit
def _gci_cpu(nir_data, green_data):
    out = np.zeros_like(nir_data, dtype='f4')
    rows, cols = nir_data.shape
    for y in range(0, rows):
        for x in range(0, cols):
            nir = nir_data[y, x]
            green = green_data[y, x]
            out[y, x] = nir / green - 1
    return out


@cuda.jit
def _gci_gpu(nir_data, green_data, out):
    y, x = cuda.grid(2)
    if y < out.shape[0] and x < out.shape[1]:
        nir = nir_data[y, x]
        green = green_data[y, x]
        out[y, x] = nir / green - 1


def _gci_dask(nir_data, green_data):
    out = da.map_blocks(_gci_cpu, nir_data, green_data, meta=np.array(()))
    return out


def _gci_cupy(nir_data, green_data):

    import cupy

    griddim, blockdim = cuda_args(nir_data.shape)
    out = cupy.empty(nir_data.shape, dtype='f4')
    out[:] = cupy.nan
    _gci_gpu[griddim, blockdim](nir_data, green_data, out)
    return out


def _gci_dask_cupy(nir_data, green_data):

    import cupy

    out = da.map_blocks(_gci_cupy, nir_data, green_data,
                        dtype=cupy.float32, meta=cupy.array(()))
    return out


def gci(nir_agg: DataArray, green_agg: DataArray, name='gci'):
    """Computes Green Chlorophyll Index

    Parameters
    ----------
    nir_agg : DataArray
        near-infrared band data

    green_agg : DataArray
        green band data

    Returns
    -------
    data: DataArray

    Notes:
    ------
    Algorithm References:
    https://en.wikipedia.org/wiki/Enhanced_vegetation_index
    """
    validate_arrays(nir_agg, green_agg)

    mapper = ArrayTypeFunctionMapping(numpy_func=_gci_cpu,
                                      dask_func=_gci_dask,
                                      cupy_func=_gci_cupy,
                                      dask_cupy_func=_gci_dask_cupy)

    out = mapper(nir_agg)(nir_agg.data, green_agg.data)

    return DataArray(out,
                     name=name,
                     coords=nir_agg.coords,
                     dims=nir_agg.dims,
                     attrs=nir_agg.attrs)


# NBR -------------
def nbr(nir_agg: DataArray, swir2_agg: DataArray, name='nbr'):
    """Computes Normalized Burn Ratio

    Parameters
    ----------
    nir_agg : DataArray
        near-infrared band

    swir_agg : DataArray
        shortwave infrared band
        (Landsat 4-7: Band 6)
        (Landsat 8: Band 7)

    Returns
    -------
    data: DataArray

    Notes:
    ------
    Algorithm References:
    https://www.usgs.gov/land-resources/nli/landsat/landsat-normalized-burn-ratio
    """

    validate_arrays(nir_agg, swir2_agg)

    mapper = ArrayTypeFunctionMapping(numpy_func=_normalized_ratio_cpu,
                                      dask_func=_run_normalized_ratio_dask,
                                      cupy_func=_run_normalized_ratio_cupy,
                                      dask_cupy_func=_run_normalized_ratio_dask_cupy)

    out = mapper(nir_agg)(nir_agg.data, swir2_agg.data)

    return DataArray(out,
                     name=name,
                     coords=nir_agg.coords,
                     dims=nir_agg.dims,
                     attrs=nir_agg.attrs)


def nbr2(swir1_agg: DataArray, swir2_agg: DataArray, name='nbr'):
    """Computes Normalized Burn Ratio 2

    "NBR2 modifies the Normalized Burn Ratio (NBR)
    to highlight water sensitivity in vegetation and
    may be useful in post-fire recovery studies."

    https://www.usgs.gov/land-resources/nli/landsat/landsat-normalized-burn-ratio-2

    Parameters
    ----------
    swir1_agg : DataArray
        near-infrared band
        shortwave infrared band
        (Landsat 4-7: Band 5)
        (Landsat 8: Band 6)

    swir2_agg : DataArray
        shortwave infrared band
        (Landsat 4-7: Band 6)
        (Landsat 8: Band 7)

    Returns
    -------
    data: DataArray

    Notes:
    ------
    Algorithm References:
    https://www.usgs.gov/land-resources/nli/landsat/landsat-normalized-burn-ratio-2
    """
    validate_arrays(swir1_agg, swir2_agg)

    mapper = ArrayTypeFunctionMapping(numpy_func=_normalized_ratio_cpu,
                                      dask_func=_run_normalized_ratio_dask,
                                      cupy_func=_run_normalized_ratio_cupy,
                                      dask_cupy_func=_run_normalized_ratio_dask_cupy)

    out = mapper(swir1_agg)(swir1_agg.data, swir2_agg.data)

    return DataArray(out,
                     name=name,
                     coords=swir1_agg.coords,
                     dims=swir1_agg.dims,
                     attrs=swir1_agg.attrs)


# NDVI -------------
def ndvi(nir_agg: DataArray, red_agg: DataArray, name='ndvi'):
    """Returns Normalized Difference Vegetation Index (NDVI).

    Parameters
    ----------
    nir_agg : DataArray
        near-infrared band data
    red_agg : DataArray
        red band data

    Returns
    -------
    data: DataArray

    Notes:
    ------
    Algorithm References:
    http://ceholden.github.io/open-geo-tutorial/python/chapter_2_indices.html
    """

    validate_arrays(nir_agg, red_agg)

    mapper = ArrayTypeFunctionMapping(numpy_func=_normalized_ratio_cpu,
                                      dask_func=_run_normalized_ratio_dask,
                                      cupy_func=_run_normalized_ratio_cupy,
                                      dask_cupy_func=_run_normalized_ratio_dask_cupy)

    out = mapper(nir_agg)(nir_agg.data, red_agg.data)

    return DataArray(out,
                     name=name,
                     coords=nir_agg.coords,
                     dims=nir_agg.dims,
                     attrs=nir_agg.attrs)


# NDMI -------------
def ndmi(nir_agg: DataArray, swir1_agg: DataArray, name='ndmi'):
    """Computes Normalized Difference Moisture Index

    Parameters
    ----------
    nir_agg : DataArray
        near-infrared band
        (Landsat 4-7: Band 4)
        (Landsat 8: Band 5)

    swir1_agg : DataArray
        shortwave infrared band
        (Landsat 4-7: Band 5)
        (Landsat 8: Band 6)


    Returns
    -------
    data: DataArray

    Notes:
    ------
    Algorithm References:
    https://www.usgs.gov/land-resources/nli/landsat/normalized-difference-moisture-index
    """

    validate_arrays(nir_agg, swir1_agg)

    mapper = ArrayTypeFunctionMapping(numpy_func=_normalized_ratio_cpu,
                                      dask_func=_run_normalized_ratio_dask,
                                      cupy_func=_run_normalized_ratio_cupy,
                                      dask_cupy_func=_run_normalized_ratio_dask_cupy)

    out = mapper(nir_agg)(nir_agg.data, swir1_agg.data)

    return DataArray(out,
                     name=name,
                     coords=nir_agg.coords,
                     dims=nir_agg.dims,
                     attrs=nir_agg.attrs)


@ngjit
def _normalized_ratio_cpu(arr1, arr2):
    out = np.zeros_like(arr1, dtype='f4')
    rows, cols = arr1.shape
    for y in range(0, rows):
        for x in range(0, cols):
            val1 = arr1[y, x]
            val2 = arr2[y, x]
            numerator = val1 - val2
            denominator = val1 + val2

            if denominator == 0.0:
                continue
            else:
                out[y, x] = numerator / denominator

    return out


def _run_normalized_ratio_dask(arr1, arr2):
    out = da.map_blocks(_normalized_ratio_cpu, arr1, arr2,
                        meta=np.array(()))
    return out


@cuda.jit
def _normalized_ratio_gpu(arr1, arr2, out):
    y, x = cuda.grid(2)
    if y < out.shape[0] and x < out.shape[1]:
        val1 = arr1[y, x]
        val2 = arr2[y, x]
        numerator = val1 - val2
        denominator = val1 + val2
        out[y, x] = numerator / denominator


def _run_normalized_ratio_cupy(arr1, arr2):

    import cupy

    griddim, blockdim = cuda_args(arr1.shape)
    out = cupy.empty(arr1.shape, dtype='f4')
    out[:] = cupy.nan
    _normalized_ratio_gpu[griddim, blockdim](arr1, arr2, out)
    return out


def _run_normalized_ratio_dask_cupy(arr1, arr2):

    import cupy

    out = da.map_blocks(_run_normalized_ratio_cupy, arr1, arr2,
                        dtype=cupy.float32, meta=cupy.array(()))
    return out


@ngjit
def _savi_cpu(nir_data, red_data, soil_factor):
    out = np.zeros_like(nir_data, dtype='f4')
    rows, cols = nir_data.shape
    for y in range(0, rows):
        for x in range(0, cols):
            nir = nir_data[y, x]
            red = red_data[y, x]
            numerator = nir - red
            soma = nir + red + soil_factor
            denominator = soma * (1.0 + soil_factor)
            out[y, x] = numerator / denominator

    return out


@cuda.jit
def _savi_gpu(nir_data, red_data, soil_factor, out):
    y, x = cuda.grid(2)
    if y < out.shape[0] and x < out.shape[1]:
        nir = nir_data[y, x]
        red = red_data[y, x]
        numerator = nir - red
        soma = nir + red + soil_factor
        denominator = soma * (nb.float32(1.0) + soil_factor)
        out[y, x] = numerator / denominator


def _savi_dask(nir_data, red_data, soil_factor):
    out = da.map_blocks(_savi_cpu, nir_data, red_data, soil_factor,
                        meta=np.array(()))
    return out


def _savi_cupy(nir_data, red_data, soil_factor):

    import cupy

    griddim, blockdim = cuda_args(nir_data.shape)
    out = cupy.empty(nir_data.shape, dtype='f4')
    out[:] = cupy.nan
    _savi_gpu[griddim, blockdim](nir_data, red_data, soil_factor, out)
    return out


def _savi_dask_cupy(nir_data, red_data, soil_factor):

    import cupy

    out = da.map_blocks(_savi_cupy, nir_data, red_data, soil_factor,
                        dtype=cupy.float32, meta=cupy.array(()))
    return out


# SAVI -------------
def savi(nir_agg: DataArray, red_agg: DataArray,
         soil_factor: float = 1.0, name: str = 'savi'):
    """Returns Soil Adjusted Vegetation Index (SAVI).

    Parameters
    ----------
    nir_agg : DataArray
        near-infrared band data

    red_agg : DataArray
        red band data

    soil_factor : float
      soil adjustment factor between -1.0 and 1.0.
      when set to zero, savi will return the same as ndvi

    Returns
    -------
    data: DataArray

    Notes:
    ------
    Algorithm References:
     - https://www.sciencedirect.com/science/article/abs/pii/003442578890106X
    """

    validate_arrays(red_agg, nir_agg)

    if not -1.0 <= soil_factor <= 1.0:
        raise ValueError("soil factor must be between [-1.0, 1.0]")

    mapper = ArrayTypeFunctionMapping(numpy_func=_savi_cpu,
                                      dask_func=_savi_dask,
                                      cupy_func=_savi_cupy,
                                      dask_cupy_func=_savi_dask_cupy)

    out = mapper(red_agg)(nir_agg.data, red_agg.data, soil_factor)

    return DataArray(out,
                     name=name,
                     coords=nir_agg.coords,
                     dims=nir_agg.dims,
                     attrs=nir_agg.attrs)


# SIPI -------------
@ngjit
def _sipi_cpu(nir_data, red_data, blue_data):
    out = np.zeros_like(nir_data, dtype='f4')
    rows, cols = nir_data.shape
    for y in range(0, rows):
        for x in range(0, cols):
            nir = nir_data[y, x]
            red = red_data[y, x]
            blue = blue_data[y, x]
            numerator = nir - blue
            denominator = nir - red
            out[y, x] = numerator / denominator
    return out


@cuda.jit
def _sipi_gpu(nir_data, red_data, blue_data, out):
    y, x = cuda.grid(2)
    if y < out.shape[0] and x < out.shape[1]:
        nir = nir_data[y, x]
        red = red_data[y, x]
        blue = blue_data[y, x]
        numerator = nir - blue
        denominator = nir - red
        out[y, x] = numerator / denominator


def _sipi_dask(nir_data, red_data, blue_data):
    out = da.map_blocks(_sipi_cpu, nir_data, red_data, blue_data,
                        meta=np.array(()))
    return out


def _sipi_cupy(nir_data, red_data, blue_data):

    import cupy

    griddim, blockdim = cuda_args(nir_data.shape)
    out = cupy.empty(nir_data.shape, dtype='f4')
    out[:] = cupy.nan
    _sipi_gpu[griddim, blockdim](nir_data, red_data, blue_data, out)
    return out


def _sipi_dask_cupy(nir_data, red_data, blue_data):

    import cupy

    out = da.map_blocks(_sipi_cupy, nir_data, red_data, blue_data,
                        dtype=cupy.float32, meta=cupy.array(()))
    return out


def sipi(nir_agg: DataArray, red_agg: DataArray, blue_agg: DataArray,
         name='sipi'):
    """Computes Structure Insensitive Pigment Index which helpful
    in early disease detection

    Parameters
    ----------
    nir_agg : DataArray
        near-infrared band data

    green_agg : DataArray
        green band data

    Returns
    -------
    data: DataArray

    Notes:
    ------
    Algorithm References:
    https://en.wikipedia.org/wiki/Enhanced_vegetation_index
    """

    validate_arrays(red_agg, nir_agg, blue_agg)

    mapper = ArrayTypeFunctionMapping(numpy_func=_sipi_cpu,
                                      dask_func=_sipi_dask,
                                      cupy_func=_sipi_cupy,
                                      dask_cupy_func=_sipi_dask_cupy)

    out = mapper(red_agg)(nir_agg.data, red_agg.data, blue_agg.data)

    return DataArray(out,
                     name=name,
                     coords=nir_agg.coords,
                     dims=nir_agg.dims,
                     attrs=nir_agg.attrs)


# EBBI -------------
@ngjit
def _ebbi_cpu(red_data, swir_data, tir_data):
    out = np.zeros_like(red_data, dtype='f4')
    rows, cols = red_data.shape
    for y in range(0, rows):
        for x in range(0, cols):
            red = red_data[y, x]
            swir = swir_data[y, x]
            tir = tir_data[y, x]
            numerator = swir - red
            denominator = 10 * np.sqrt(swir + tir)
            out[y, x] = numerator / denominator
    return out


@cuda.jit
def _ebbi_gpu(red_data, swir_data, tir_data, out):
    y, x = cuda.grid(2)
    if y < out.shape[0] and x < out.shape[1]:
        red = red_data[y, x]
        swir = swir_data[y, x]
        tir = tir_data[y, x]
        numerator = swir - red
        denominator = nb.int64(10) * sqrt(swir + tir)
        out[y, x] = numerator / denominator


def _ebbi_dask(red_data, swir_data, tir_data):
    out = da.map_blocks(_ebbi_cpu, red_data, swir_data, tir_data,
                        meta=np.array(()))
    return out


def _ebbi_cupy(red_data, swir_data, tir_data):

    import cupy

    griddim, blockdim = cuda_args(red_data.shape)
    out = cupy.empty(red_data.shape, dtype='f4')
    out[:] = cupy.nan
    _ebbi_gpu[griddim, blockdim](red_data, swir_data, tir_data, out)
    return out


def _ebbi_dask_cupy(red_data, swir_data, tir_data):

    import cupy

    out = da.map_blocks(_ebbi_cupy, red_data, swir_data, tir_data,
                        dtype=cupy.float32, meta=cupy.array(()))
    return out


def ebbi(red_agg: DataArray, swir_agg: DataArray, tir_agg: DataArray,
         name='ebbi'):
    """Computes Enhanced Built-Up and Bareness Index

    Parameters
    ----------
    red_agg : DataArray
        red band data
    swir_agg : DataArray
        shortwave infrared band data
    tir_agg : DataArray
        thermal infrared band data

    Returns
    -------
    data: DataArray

    Notes:
    ------
    Algorithm References:
    https://rdrr.io/cran/LSRS/man/EBBI.html
    """
    validate_arrays(red_agg, swir_agg, tir_agg)

    mapper = ArrayTypeFunctionMapping(numpy_func=_ebbi_cpu,
                                      dask_func=_ebbi_dask,
                                      cupy_func=_ebbi_cupy,
                                      dask_cupy_func=_ebbi_dask_cupy)

    out = mapper(red_agg)(red_agg.data, swir_agg.data, tir_agg.data)

    return DataArray(out,
                     name=name,
                     coords=red_agg.coords,
                     dims=red_agg.dims,
                     attrs=red_agg.attrs)


@ngjit
def _normalize_data(agg, pixel_max=255.0):
    out = np.zeros_like(agg)
    min_val = 0
    max_val = 2**16 - 1
    range_val = max_val - min_val
    rows, cols = agg.shape
    c = 40
    th = .125
    for y in range(rows):
        for x in range(cols):
            val = agg[y, x]
            norm = (val - min_val) / range_val

            # sigmoid contrast enhancement
            norm = 1 / (1 + np.exp(c * (th - norm)))
            out[y, x] = norm * pixel_max
    return out


def true_color(r, g, b, nodata=1):
    h, w = r.shape

    data = np.zeros((h, w, 4), dtype=np.uint8)
    data[:, :, 0] = (_normalize_data(r)).astype(np.uint8)
    data[:, :, 1] = (_normalize_data(g)).astype(np.uint8)
    data[:, :, 2] = (_normalize_data(b)).astype(np.uint8)

    a = np.where(np.logical_or(np.isnan(r), r <= nodata), 0, 255)
    data[:, :, 3] = a.astype(np.uint8)

    return Image.fromarray(data, 'RGBA')
