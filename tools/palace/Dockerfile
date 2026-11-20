# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
#
# Palace (https://github.com/awslabs/palace, Apache-2.0) in a container, to
# run the EM models PackageDesigner writes. Palace has no Windows build and no
# published image; this builds a release from source as its install guide
# describes (CMake superbuild: MFEM, libCEED, Hypre, SuperLU_DIST, SLEPc/PETSc,
# ... are downloaded and built), on Ubuntu 24.04 with GCC 13, OpenMPI and
# serial OpenBLAS. CPU only.
#
#   docker build -t pkgdesigner/palace:0.18.1 --build-arg JOBS=16 .
#
# The build takes about half an hour to an hour; the image keeps only the
# installed Palace and the libraries it links against.

FROM ubuntu:24.04 AS build
ARG PALACE_VERSION=v0.18.1
ARG JOBS=8
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gfortran cmake git ca-certificates curl pkg-config python3 \
        libopenmpi-dev openmpi-bin libopenblas-serial-dev liblapack-dev \
        libunwind-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch ${PALACE_VERSION} --recurse-submodules \
        https://github.com/awslabs/palace.git /src/palace
# MAGMA is for GPUs only; everything else stays at Palace's defaults
# (SuperLU_DIST direct solver, SLEPc eigensolver, libxsmm, GSLIB, SUNDIALS).
RUN cmake -S /src/palace -B /build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/palace \
        -DBLA_VENDOR=OpenBLAS \
        -DPALACE_WITH_MAGMA=OFF \
        -DPALACE_GIT_COMMIT_ID=${PALACE_VERSION} \
        -DPALACE_BUILD_ID=pkgdesigner \
    && cmake --build /build -j ${JOBS} \
    && (cmake --install /build 2>/dev/null || true) \
    && test -x /opt/palace/bin/palace \
    && ls /opt/palace/bin/palace-*.bin

FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        openmpi-bin libopenmpi3t64 libopenblas0-serial liblapack3 libgfortran5 libgomp1 \
        libunwind8 zlib1g ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/palace /opt/palace
# Every library the binary needs must be here: fail the build, not the first run.
RUN ! (ldd /opt/palace/bin/palace-*.bin | grep "not found")
# Not root: mpirun refuses to run as root. Ubuntu 24.04 images already have
# the unprivileged user "ubuntu" (uid 1000).
USER ubuntu
ENV PATH=/opt/palace/bin:$PATH \
    OMPI_MCA_btl_vader_single_copy_mechanism=none \
    OMPI_MCA_rmaps_base_oversubscribe=1
WORKDIR /work
ENTRYPOINT ["palace"]
CMD ["--help"]
