# -*- coding: utf-8 -*-
"""Small matplotlib compatibility helpers for older Win7/Anaconda installs."""


def install_pyplot_compat(plt=None):
    """Make newer pyplot plotting calls safe on older matplotlib versions.

    The project code uses modern conveniences such as ``layout="constrained"``
    and ``plt.subplot_mosaic``. Some Win7-era Anaconda matplotlib builds do not
    support them, so this shim downgrades those calls instead of letting a
    completed measurement fail while saving figures.
    """
    if plt is None:
        import matplotlib.pyplot as plt  # noqa: WPS433

    if not getattr(plt, "_microprobe_layout_compat_installed", False):
        original_figure = plt.figure
        original_subplots = plt.subplots

        def _normalize_layout_kwargs(kwargs):
            kwargs = dict(kwargs)
            layout = kwargs.pop("layout", None)
            if layout == "constrained" and "constrained_layout" not in kwargs:
                kwargs["constrained_layout"] = True
            return kwargs

        def _retry_without_constrained(func, args, kwargs, exc):
            if "constrained_layout" in kwargs and "unexpected keyword argument" in str(exc):
                kwargs = dict(kwargs)
                kwargs.pop("constrained_layout", None)
                return func(*args, **kwargs)
            raise exc

        def _figure(*args, **kwargs):
            kwargs = _normalize_layout_kwargs(kwargs)
            try:
                return original_figure(*args, **kwargs)
            except TypeError as exc:
                return _retry_without_constrained(original_figure, args, kwargs, exc)

        def _subplots(*args, **kwargs):
            kwargs = _normalize_layout_kwargs(kwargs)
            try:
                return original_subplots(*args, **kwargs)
            except TypeError as exc:
                return _retry_without_constrained(original_subplots, args, kwargs, exc)

        plt.figure = _figure
        plt.subplots = _subplots
        plt._microprobe_layout_compat_installed = True

    original_subplot_mosaic = getattr(plt, "subplot_mosaic", None)
    if original_subplot_mosaic is not None and not getattr(
        plt, "_microprobe_subplot_mosaic_wrapped", False
    ):
        def _wrapped_subplot_mosaic(mosaic, *args, **kwargs):
            try:
                return original_subplot_mosaic(mosaic, *args, **kwargs)
            except TypeError as exc:
                if "layout" not in kwargs or "unexpected keyword argument" not in str(exc):
                    raise
                kwargs = dict(kwargs)
                layout = kwargs.pop("layout", None)
                if layout == "constrained" and "constrained_layout" not in kwargs:
                    kwargs["constrained_layout"] = True
                try:
                    return original_subplot_mosaic(mosaic, *args, **kwargs)
                except TypeError:
                    kwargs.pop("constrained_layout", None)
                    return original_subplot_mosaic(mosaic, *args, **kwargs)

        plt.subplot_mosaic = _wrapped_subplot_mosaic
        plt._microprobe_subplot_mosaic_wrapped = True
        return plt

    if hasattr(plt, "subplot_mosaic"):
        return plt

    def _subplot_mosaic(mosaic, *, figsize=None, layout=None, **kwargs):
        from matplotlib.gridspec import GridSpec  # noqa: WPS433

        rows = [list(row) if isinstance(row, str) else list(row) for row in mosaic]
        labels = []
        for row in rows:
            for label in row:
                if label not in labels and label not in (None, "."):
                    labels.append(label)

        nrows = len(rows)
        ncols = max(len(row) for row in rows)
        fig = plt.figure(figsize=figsize, layout=layout)
        try:
            grid = GridSpec(nrows, ncols, figure=fig)
        except TypeError:
            grid = GridSpec(nrows, ncols)

        axes = {}
        for label in labels:
            cells = [
                (r, c)
                for r, row in enumerate(rows)
                for c, value in enumerate(row)
                if value == label
            ]
            row_ids = [cell[0] for cell in cells]
            col_ids = [cell[1] for cell in cells]
            axes[label] = fig.add_subplot(
                grid[min(row_ids):max(row_ids) + 1, min(col_ids):max(col_ids) + 1],
                **kwargs,
            )
        return fig, axes

    plt.subplot_mosaic = _subplot_mosaic
    return plt
