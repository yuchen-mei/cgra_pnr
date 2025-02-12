#!/usr/bin/env bash
set -e

if [[ "$OS" == "linux" ]]; then
    if [[ "$BUILD_WHEEL" == true ]]; then
        # 1. Pull the Docker image
        docker pull stanfordaha/garnet:latest

        # 2. Run the container, mounting the local cgra_pnr repo
        docker run -d --name garnet_container --rm -it \
            stanfordaha/garnet:latest bash

        # 3. Checkout the current branch inside the container
        docker exec -i garnet_container bash -c '
            git config --global --add safe.directory /aha/cgra_pnr
            set -e
            cd /aha/cgra_pnr
            CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
            git checkout "$CURRENT_BRANCH"
        '

        # 4. Build the placer (thunder)
        docker exec -i garnet_container bash -c '
            set -e
            cd /aha/cgra_pnr/thunder
            mkdir -p build
            cd build
            cmake .. -DCMAKE_BUILD_TYPE=Release
            make -j placer
        '

        # 5. Build the router (cyclone)
        docker exec -i garnet_container bash -c '
            set -e
            cd /aha/cgra_pnr/cyclone
            mkdir -p build
            cd build
            cmake .. -DCMAKE_BUILD_TYPE=Release
            make -j router
        '

        # 6. pip install -e for thunder and cyclone
        docker exec -i garnet_container bash -c '
            set -e
            cd /aha/cgra_pnr/thunder
            pip install -e .
            cd /aha/cgra_pnr/cyclone
            pip install -e .
        '

        # 7. Run aha map and aha pnr
        docker exec -i garnet_container bash -c '
            set -e
            cd /aha/cgra_pnr
            source /aha/bin/activate
            aha map apps/pointwise
            aha pnr apps/pointwise --width 4 --height 4
        '
    fi

elif [[ "$OS" == "osx" ]]; then
    python --version
    cd thunder && CXX=g++-9 python setup.py bdist_wheel
    pip install dist/*.whl
    cd ..
    cd cyclone && CXX=g++-9 python setup.py bdist_wheel
    pip install dist/*.whl
    cd ..
    pytest -v tests/
    mkdir dist && cp thunder/dist/* dist/ && cp cyclone/dist/* dist/
else
    python --version
    python -m pip install wheel pytest twine
    python setup.py bdist_wheel
    python -m pip install --find-links=dist kratos
    python -m pytest -v tests/
fi

echo [distutils]                                  > ~/.pypirc
echo index-servers =                             >> ~/.pypirc
echo "  pypi"                                    >> ~/.pypirc
echo                                             >> ~/.pypirc
echo [pypi]                                      >> ~/.pypirc
echo repository=https://upload.pypi.org/legacy/  >> ~/.pypirc
echo username=keyi                               >> ~/.pypirc
echo password=$PYPI_PASSWORD                     >> ~/.pypirc
