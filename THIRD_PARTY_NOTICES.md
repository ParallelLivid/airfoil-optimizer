# Third-party components

This source release contains the Python wrapper, its tests, and documentation. The MIT license in `LICENSE` applies to those files. It does not relicense external software.

## XFOIL

XFOIL is developed by Mark Drela and Harold Youngren. The tested XFOIL 6.99 source header identifies GNU GPL version 2 or later. Upstream distribution terms and downloads are available from the [official XFOIL site](https://web.mit.edu/drela/Public/web/xfoil/); its [user guide](https://web.mit.edu/drela/Public/web/xfoil/xfoil_doc.txt) describes the normalization and coefficient conventions used by this wrapper.

No XFOIL, Pplot, Pxplot executables, source archives, or plotting libraries are included in the Git release or source ZIP. Install XFOIL separately from upstream, retain its notices, and follow its terms if you redistribute it. Existing local copies are excluded by `.gitignore` and the release manifest. The wrapper does not require the optional Pplot or Pxplot executables.

The local Windows XFOIL 6.99 executable used for validation had SHA-256:

```text
c17342f84ae260c2b11a74cd0e2fb8189a5f8954c6bb7a8467a0f27055c7faea
```

That executable predated this release preparation. Its original download provenance was not authenticated; the hash records the tested artifact and is not a claim that an upstream download was verified.

## Python dependencies

NumPy and Matplotlib are installed separately from `requirements.txt`, not vendored. Their packages include their respective license notices and dependencies. See the [NumPy license](https://github.com/numpy/numpy/blob/main/LICENSE.txt) and [Matplotlib license](https://matplotlib.org/stable/project/license.html).
