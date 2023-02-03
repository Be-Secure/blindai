
VERSION --use-cache-command --use-copy-include-patterns --wait-block 0.6

ci:
    BUILD +dev-tests
    BUILD +dev-unit-tests

    BUILD +build-release-enclave
    BUILD +build-release-runner
    BUILD +build-release-client
    BUILD +test-release

    # BUILD +build-docker-image
    # BUILD +test-docker-image

publish:
    # Make sure the CI runs successfully
    WAIT 
        BUILD +ci
    END
    
    BUILD +publish-client-release

dev-image:
    FROM DOCKERFILE -f .devcontainer/Dockerfile .
    WORKDIR /blindai-preview

dev-image-poetry:
    FROM +dev-image

    # install python depencies
    COPY client/pyproject.toml client/poetry.lock ./client/

    CACHE /root/.cache/pypoetry
    RUN poetry install --directory ./client --no-root


prepare-test:
    FROM +dev-image-poetry
    
    # generate tests onnx models and inputs
    COPY tests tests
    # Cache Hugging Face models
    CACHE /root/.cache/huggingface/hub/
    # Cache mobilenet
    CACHE tests/mobilenet/.cache
    RUN cd tests && bash generate_all_onnx_and_npz.sh

dev-tests:
    FROM +prepare-test

    CACHE /usr/local/cargo/git
    CACHE /usr/local/cargo/registry
    CACHE /blindai-preview/target

    COPY justfile Cargo.toml Cargo.lock ./
    COPY .cargo .cargo
    COPY tar-rs-sgx tar-rs-sgx
    COPY tract tract
    COPY ring-fortanix ring-fortanix
    COPY rouille rouille
    COPY tiny-http tiny-http

    # compile Rust sources for the enclave
    COPY src src
    RUN cargo build --target x86_64-fortanix-unknown-sgx \
        && cargo build --target x86_64-fortanix-unknown-sgx --release

    # compile Rust sources for the runner
    COPY runner runner
    CACHE /blindai-preview/runner/target
    RUN cd runner \
        && cargo check \
        && cargo build --release

    # cargo fmt, clippy and audit
    RUN cargo fmt --check \
        && cargo clippy --target x86_64-fortanix-unknown-sgx -p blindai_server -- --no-deps -Dwarnings \
        && cargo audit

    # install python client and type stubs
    COPY client client
    RUN cd client \
        && poetry install

    # python formatting checks and unit tests
    RUN cd client \
        && poetry run black --check . \
        && poetry run mypy --install-types --non-interactive --ignore-missing-imports --follow-imports=skip \
        && poetry run pytest --ignore=tests/integration_test.py 

    COPY manifest.dev.template.toml manifest.prod.template.toml ./

    # end-to-end tests

     RUN --privileged \
         --mount=type=bind-experimental,target=/var/run/aesmd/aesm.socket,source=/var/run/aesmd/aesm.socket  \
         --mount=type=bind-experimental,target=/dev/sgx/,source=/dev/sgx/  \
        ( cd /opt/intel/sgx-dcap-pccs && npm start pm2 ) & \
        just run --release & \ 
        sleep 15 \
        && cd tests \
        && bash run_all_end_to_end_tests.sh

dev-unit-tests:
    FROM +dev-image

    CACHE /usr/local/cargo/git
    CACHE /usr/local/cargo/registry
    CACHE /blindai-preview/target

    COPY tests/mobilenet tests/mobilenet
    RUN cd tests/mobilenet && bash ./setup.sh

    COPY src src
    COPY justfile Cargo.toml Cargo.lock ./
    COPY .cargo .cargo
    COPY tar-rs-sgx tar-rs-sgx
    COPY tract tract
    COPY ring-fortanix ring-fortanix
    COPY rouille rouille
    COPY tiny-http tiny-http


    # unit tests
    RUN --privileged \
        --mount=type=bind-experimental,target=/var/run/aesmd/aesm.socket,source=/var/run/aesmd/aesm.socket  \
        --mount=type=bind-experimental,target=/dev/sgx/,source=/dev/sgx/  \
        cargo test --target x86_64-fortanix-unknown-sgx --release


build-release-enclave:
    # Minimal image to build the release version of the sgx enclave
    FROM rust:1.66.1-slim-bullseye
    WORKDIR blindai-preview

    # Install dependencies and pre-install the rust toolchain declared via rust-toolchain.toml 
    # for better caching
    RUN --mount=type=cache,target=/var/cache/apt,id=apt-cache-build-release-enclave \ 
        apt-get update \
        && apt-get install --no-install-recommends -y \
            protobuf-compiler=3.12.4-1 \
            pkg-config=0.29.2-1 \
            libssl-dev=1.1.1n-0+deb11u3 \
            gettext-base \
        && rm -rf /var/lib/apt/lists/* \
        && rustup default nightly-2023-01-11 \
        && rustup target add x86_64-fortanix-unknown-sgx

    CACHE /usr/local/cargo/git
    CACHE /usr/local/cargo/registry

    RUN cargo install --locked --git https://github.com/mithril-security/rust-sgx.git --tag fortanix-sgx-tools_v0.5.1-mithril fortanix-sgx-tools sgxs-tools

    COPY Cargo.toml Cargo.lock rust-toolchain.toml  manifest.prod.template.toml ./
    COPY .cargo .cargo
    COPY src src
    COPY tar-rs-sgx tar-rs-sgx
    COPY tract tract
    COPY ring-fortanix ring-fortanix
    COPY tiny-http tiny-http
    COPY rouille rouille

    CACHE target

    RUN cargo build --locked --release --target "x86_64-fortanix-unknown-sgx"

    ENV BIN_PATH=target/x86_64-fortanix-unknown-sgx/release/blindai_server

    RUN ftxsgx-elf2sgxs target/x86_64-fortanix-unknown-sgx/release/blindai_server --heap-size 0xFBA00000 --stack-size 0x400000 --threads 20 \
        && mr_enclave=`sgxs-hash "$BIN_PATH.sgxs"` envsubst < manifest.prod.template.toml > manifest.toml

    RUN openssl genrsa -3 3072 > throw_away.pem \
        && sgxs-sign --key throw_away.pem "$BIN_PATH.sgxs" "$BIN_PATH.sig" --xfrm 7/0 --isvprodid 0 --isvsvn 0 \
        && rm throw_away.pem

    SAVE ARTIFACT $BIN_PATH.sgxs
    SAVE ARTIFACT $BIN_PATH.sig
    SAVE ARTIFACT manifest.toml

build-release-runner:
    # Build the release version of the runner
    FROM rust:1.66.1-slim-bullseye

    RUN rustup set profile minimal \
        && rustup default nightly-2023-01-11

    RUN apt-get update \
        && apt-get install -y --no-install-recommends pkg-config protobuf-compiler libssl-dev curl gnupg software-properties-common  \
        && curl -fsSL  https://download.01.org/intel-sgx/sgx_repo/ubuntu/intel-sgx-deb.key | apt-key add - \
        && add-apt-repository "deb https://download.01.org/intel-sgx/sgx_repo/ubuntu focal main" \
        && apt-get update \
        && apt-get install -y --no-install-recommends libsgx-dcap-default-qpl \
        && rm -rf /var/lib/apt/lists/* \
        && ln -s /usr/lib/x86_64-linux-gnu/libdcap_quoteprov.so.1 /usr/lib/x86_64-linux-gnu/libdcap_quoteprov.so 

    WORKDIR blindai-preview
    COPY runner runner

    CACHE /usr/local/cargo/git
    CACHE /usr/local/cargo/registry
    CACHE runner/target

    RUN cd runner \
        && cargo build --release

    SAVE ARTIFACT runner/target/release/runner

build-release-client:
    FROM python:3.10.9-alpine3.17
    WORKDIR blindai-preview

    RUN pip install poetry 

    COPY client client
    COPY +build-release-enclave/manifest.toml client/blindai_preview
    RUN cd client \
        && poetry build
    SAVE ARTIFACT client/dist

publish-client-release:
    FROM +build-release-client

    RUN --push --secret API_TOKEN_PYPI \ 
        cd client \
        && POETRY_PYPI_TOKEN_PYPI="$API_TOKEN_PYPI" poetry publish

test-release:
    FROM +prepare-test

    COPY +build-release-client/dist/*.whl ./
    COPY +build-release-enclave/blindai_server.sgxs ./
    COPY +build-release-runner/runner ./

    RUN cd client && poetry run pip install  ../*.whl

    RUN openssl genrsa -3 3072 > throw_away_key.pem \
        && sgxs-sign --key throw_away_key.pem  blindai_server.sgxs blindai_server.sig --xfrm 7/0 --isvprodid 0 --isvsvn 0

    RUN --privileged \
        --mount=type=bind-experimental,target=/var/run/aesmd/aesm.socket,source=/var/run/aesmd/aesm.socket  \
        --mount=type=bind-experimental,target=/dev/sgx/,source=/dev/sgx/  \
        ( cd /opt/intel/sgx-dcap-pccs && npm start pm2 ) & \
         ./runner blindai_server.sgxs & \
         sleep 15 \
        && cd tests \
        && bash run_all_end_to_end_tests.sh

build-docker-image:
    # Minimal image to run blindai
    FROM ubuntu:20.04

    WORKDIR /root

    COPY .devcontainer/setup-pccs.sh /root/

    RUN \
        # Install temp dependencies
        TEMP_DEPENDENCIES="curl lsb-release gnupg2" \
        && apt-get update -y && apt-get install -y $TEMP_DEPENDENCIES \

        # Configure Intel APT repository
        && echo "deb https://download.01.org/intel-sgx/sgx_repo/ubuntu $(lsb_release -cs) main" | tee -a /etc/apt/sources.list.d/intel-sgx.list >/dev/null \
        && curl -sSL "https://download.01.org/intel-sgx/sgx_repo/ubuntu/intel-sgx-deb.key" | apt-key add - \
        && apt-get update -y \

        # Install nodejs and cracklib-runtime (dependencies of sgx-dcap-pccs)
        && curl -sL https://deb.nodesource.com/setup_14.x | bash - \
        && apt-get install --no-install-recommends -y nodejs cracklib-runtime \

        # A regular install with `apt-get install -y sgx-dcap-pccs` would fail with :
        # ```
        # Installing PCCS service ... failed.
        # Unsupported platform - neither systemctl nor initctl was found.
        # ```
        # We get around this by downloading the deb package and removing the post installation script
        # and we then do the configuration ourselves with the "setup-pccs.sh" script.
        # It's a bit hacky but it works.
        && apt-get download -y sgx-dcap-pccs \
        && dpkg --unpack sgx-dcap-pccs_*.deb \
        && rm sgx-dcap-pccs_*.deb \
        && rm -f /var/lib/dpkg/info/sgx-dcap-pccs.postinst \
        && dpkg --configure sgx-dcap-pccs || true \
        && apt-get install --no-install-recommends -yf \
        && ./setup-pccs.sh \

        # Install and configure DCAP Quote Provider Library (QPL)
        && apt-get install --no-install-recommends -y libsgx-dcap-default-qpl \
        # Update sgx_default_qcnl.conf to reflect the fact that 
        # we configured the PCCS to use self-signed certificates.
        && sed -i 's/"use_secure_cert": true/"use_secure_cert": false/g' /etc/sgx_default_qcnl.conf \

        # Remove temp dependencies
        && apt-get remove -y $TEMP_DEPENDENCIES && apt-get autoremove -y \
        && rm -rf /var/lib/apt/lists/* && rm -rf /var/cache/apt/archives/*

    COPY .devcontainer/hw-start.sh /root/start.sh

    COPY +build-release-enclave/blindai_server.sgxs \
         +build-release-enclave/blindai_server.sig \
         +build-release-runner/runner \
         ./

    EXPOSE 9923
    EXPOSE 9924

    CMD ./start.sh

    SAVE IMAGE  blindai-docker-xyz:latest

test-docker-image:
    FROM +prepare-test

    COPY .earthly/docker-auto-install.sh .
    RUN sh docker-auto-install.sh

    COPY +build-release-client/dist/*.whl ./
    RUN cd client && poetry run pip install  ../*.whl

    WITH DOCKER --load=blindai-docker-xyz:latest=+build-docker-image
        RUN --privileged \
        --mount=type=bind-experimental,target=/var/run/aesmd/aesm.socket,source=/var/run/aesmd/aesm.socket  \
        --mount=type=bind-experimental,target=/dev/sgx/,source=/dev/sgx/  \
            docker run \
            --privileged \
            -p 127.0.0.1:9923:9923 \
            -p 127.0.0.1:9924:9924 \
            --mount type=bind,source=/dev/sgx,target=/dev/sgx \
            -v /var/run/aesmd/aesm.socket:/var/run/aesmd/aesm.socket \
            blindai-docker-xyz:latest & \
            sleep 30 \
            && cd tests \
            && bash run_all_end_to_end_tests.sh
    END
