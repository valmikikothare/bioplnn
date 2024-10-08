## BioPlNN: Biologically Plausible Neural Network Package

**BioPlNN** is a PyTorch package designed to bridge the gap between traditional Artificial Neural Networks (ANNs) and biologically-inspired models. It provides modules that allow researchers to:

* Simulate large-scale populations of neurons with realistic biological properties.
* Explore the impact of network topology on neural function.
* Train models using standard machine learning techniques while incorporating biological constraints.

### Key Features

* **TopographicalRNN:** This module simulates a population of rate-based neurons with arbitrary connectivity patterns. It utilizes sparse tensors for efficient memory usage, enabling simulations of large-scale networks.
* **Conv2dEIRNN:** This module builds upon PyTorch's existing Conv2d and RNN modules by introducing separate excitatory and inhibitory neural populations within each layer. It allows users to define the connectivity between these populations within and across layers.

### Installation

**Using pip:**

The recommended installation method is via pip:

```bash
pip install bioplnn
```

**Building from source:**

1. Clone the BioPlNN repository:

```bash
git clone https://github.com/hpvok13/bioplnn.git
```

2. Navigate to the cloned directory:

```bash
cd bioplnn
```

3. Build and install the package:

```bash
pip install [-e] .
```

### Usage

**TopographicalRNN:**

```python
import torch
from bioplnn.models import TopographicalRNN

# Create RNN layer
rnn = TopographicalRNN(
    num_classes=10,
    connectivity_hh="connectivity_hh.pt",
    connectivity_ih="connectivity_ih.pt",
    input_indices="input_indices.pt",
    output_indices="output_indices.pt",
    batch_first=True,
)

# Define input data (num_neurons must match the number of neurons in the connectivity
# matrices or the number of neurons in the input_indices tensor)
inputs = torch.rand(batch_size, num_neurons)

# Run simulation
outputs = rnn(inputs)
```

**Conv2dEIRNN:**

```python
import torch
from bioplnn import Conv2dEIRNN

# Define excitatory and inhibitory kernel sizes
in_size = (32, 32)
in_channels = 3
h_pyr_channels = [16, 32]
h_inter_channels = [[16, 16], [32, 32]]
fb_channels = [16, 32]
fb_adjacency = [[0, 0], [1, 0]]
num_layers = 2
batch_first = True

# Create Conv2dEIRNN layer
rnn = Conv2dEIRNN(
    in_size=in_size,
    in_channels=in_channels,
    h_pyr_channels=h_pyr_channels,
    h_inter_channels=h_inter_channels,
    fb_channels=fb_channels,
    fb_adjacency=fb_adjacency,
    num_layers=num_layers,
    batch_first=batch_first,
)

# Define input data
inputs = torch.rand(batch_size, in_channels, height, width)

# Run forward pass
outputs = rnn(inputs)
```

**Further Documentation:**

This README provides a basic introduction to BioPlNN. More detailed documentation, including advanced usage examples and configuration options, will be available soon. Example are provided in the `examples` directory.

**Contributing:**

We welcome contributions to BioPlNN! Please refer to the CONTRIBUTING.md file for guidelines on submitting code and documentation changes.
