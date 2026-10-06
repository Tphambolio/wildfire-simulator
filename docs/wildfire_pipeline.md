# 3D Wildfire Digital Twin Research Pipeline

This document outlines the end-to-end technical workflow to convert high-resolution UAV-LiDAR data (DJI Terra) into a functioning 3D fuel grid and dynamic fire simulation within a digital twin environment.

## Phase 1: Data Acquisition & DJI Terra Pre-Processing
The goal of this phase is to move from raw LiDAR signals to a clean, georeferenced, and classified point cloud.

1.  **LiDAR Reconstruction:** Import raw L1/L2 data folders (containing CLC, CLI, CMI, etc.) into DJI Terra. Select **High Density** (100%) to preserve fine-scale fuel structures.
2.  **Point Cloud Optimization:** Enable **Optimize Point Cloud Accuracy** to improve consistency across different flight lines.
3.  **Vegetation Classification:** 
    *   Enable **Ground Point Classification**.
    *   Adjust **Iteration Angle** and **Iteration Distance** (e.g., 6.0° and 0.5 m for gentle slopes) to ensure a clean Digital Terrain Model (DTM).
4.  **Smoothing:** Enable **Smooth Point Cloud** to reduce discrete noise that could interfere with subsequent voxel bulk density calculations.
5.  **Export:** Save the results as a `.las` file (ASPRS LAS format).

---

## Phase 2: Linux Terminal Environment Setup
This phase establishes the open-source software stack required for Linux-based data processing and simulation.

1.  **System Dependencies:** Install essential compilers and libraries.
    ```bash
    sudo apt update && sudo apt install -y build-essential gfortran libnetcdf-dev \
        r-base-core libgdal-dev libgeos-dev libproj-dev cmake
    ```
2.  **Conda Environment:** Create an isolated Python environment for modeling tools.
    ```bash
    conda create -n wildfire_twin python=3.10 -y
    conda activate wildfire_twin
    pip install fastfuels-sdk quicfire-tools
    ```
3.  **LiDAR Analysis (R):** Install the `lidR` package for forestry-specific point cloud manipulation.
    ```R
    # Run in R
    install.packages("lidR")
    library(lidR)
    ```

---

## Phase 3: Voxelization and Structural Modeling
Convert the geometric point cloud into a structured 3D grid where each cell (voxel) holds fuel information.[1, 2]

1.  **Point Cloud Voxelization:** Use `lidR` to reduce point density and create a structural grid. 
    *   *Resolution Tip:* High-fidelity fire behavior models generally require voxel resolutions between 2 cm and 8 cm to match observed mass loss dynamics accurately.[2]
    ```R
    # Example lidR command
    las <- readLAS("classified_forest.las")
    voxels <- voxelize_points(las, res = 0.1) # 10cm voxels
    writeLAS(voxels, "forest_grid.las")
    ```
2.  **Classification & Ladder Fuel Detection:** Identify ladder fuels (e.g., understory vegetation between 2 m and 4 m) which are critical predictors of fire severity.[3, 4, 5] Use algorithms like **SegFormer** or **QSM** to isolate wood from foliage for better volume estimation.[6, 7]

---

## Phase 4: 3D Fuel Grid Generation
Transform the structural voxels into physical fuel inputs (Bulk Density, Moisture, $S:V$).

1.  **FastFuels Platform:** Use the FastFuels API to integrate your site-specific LiDAR data into 3D fuel arrangements.[8]
2.  **Trees Program (Pre-processor):** Build the binary fuel arrays required by physics-based models.
    ```bash
    git clone https://github.com/lanl/Trees.git
    cd Trees && make
   ./trees.exe  # Processes 'fuellist' to create treesrhof.dat and treesmoist.dat
    ```
3.  **Physical Parameters:** Assign bulk density based on LiDAR signal density.[1] 
    *   **Bulk Density ($\rho_f$):** Mass of available fuel per unit volume of the canopy.[9, 3]
    *   **Fuel Moisture:** Input live and dead fuel moisture content (LFMC/DFMC) using systems like the **Near Real-Time Fuel Moisture System**.[10, 11]

---

## Phase 5: Physics-Based Fire Simulation
Execute the simulation using a coupled fire-atmosphere engine.

1.  **QUIC-Fire Execution:** Use `quicfire-tools` to manage input decks. 
    *   The model uses a cellular automata fire spread model (Fire-CA) coupled with a 3D wind solver (QUIC-URB).
    *   The solver calculates interactions based on the Navier-Stokes equations:
        $$\frac{\partial u}{\partial t} = -(u \cdot \nabla)u - \frac{1}{\rho}\nabla p + f$$
2.  **Simulation Output:** The results are typically exported in **NetCDF** format, capturing spatiotemporal variables like:
    *   **Reaction Coordinate:** Tracks remaining fuel as it burns.
    *   **Heat Release Rate (HRR):** Measures the intensity of the fire front.[12]

---

## Phase 6: Digital Twin Visualization
Link the simulation data to a photorealistic 3D environment.

1.  **Visualization Environment:** Use **Unity** or **Unreal Engine 5** to create the virtual replica.
2.  **Volumetric Rendering (OpenVDB):**
    *   Convert NetCDF simulation data to **OpenVDB** sequences.
    *   In Unreal Engine 5.3+, import these as **Sparse Volumetric Textures (SVT)** to render smoke and fire.
3.  **Dynamic Fire Effects:**
    *   In Unity, use **Particle Systems** (Shuriken or VFX Graph) to represent flames.
    *   Drive particle color and density using the **Reaction Coordinate** and **Temperature** data exported from the simulation.
4.  **Integration Layers:** Use platforms like **Prespective** to layer the simulation framework directly on top of the Unity rendering engine for highly accurate, real-time decision support.
