import math
from typing import Optional
from warnings import warn

import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn as nn
import torch_sparse
import torchsparsegradutils as tsgu
from matplotlib import animation

from bioplnn.models.sparse_rnn import SparseRNN
from bioplnn.utils import get_activation_class, idx_1D_to_2D, idx_2D_to_1D


class TopographicalCorticalCell(nn.Module):
    def __init__(
        self,
        sheet_size: tuple[int, int] = (100, 100),
        connectivity_std: float = 10,
        synapses_per_neuron: int = 32,
        bias: bool = True,
        mm_function: str = "torch_sparse",
        sparse_layout: str = None,
        batch_first: bool = True,
        adjacency_matrix_path: str | None = None,
        self_recurrence: bool = False,
    ):
        """
        Initialize the TopographicalCorticalCell object.

        Args:
            sheet_size (tuple): The size of the sheet (number of rows, number of columns).
            connectivity_std (float): The standard deviation of the connectivity weights.
            synapses_per_neuron (int): The number of synapses per neuron.
            bias (bool, optional): Whether to include bias or not. Defaults to True.
            mm_function (str, optional): The sparse matrix multiplication function to use.
                Possible values are  'torch_sparse', 'native', and 'tsgu'. Defaults to 'torch_sparse'.
            sparse_layout (str, optional): The sparse format to use.
                Possible values are 'coo' and 'csr'. Defaults to 'coo'.
            batch_first (bool, optional): Whether the batch dimension is the first dimension. Defaults to False.
            adjacency_matrix_path (str, optional): The path to the adjacency matrix file. Defaults to None.
            self_recurrence (bool, optional): Whether to include self-recurrence connections. Defaults to False.

        Raises:
            ValueError: If an invalid mm_function or sparse_layout is provided.
        """
        super().__init__()
        # Save the sparse matrix multiplication function
        self.sheet_size = sheet_size
        self.sparse_layout = sparse_layout
        self.batch_first = batch_first

        # Select the sparse matrix multiplication function
        if mm_function == "torch_sparse":
            if sparse_layout != "torch_sparse":
                raise ValueError(
                    "sparse_layout must be 'torch_sparse' or None if mm_function is 'torch_sparse'"
                )
            self.sparse_layout = "torch_sparse"
            self.mm_function = torch_sparse.spmm
        elif mm_function in ("native", "tsgu"):
            if sparse_layout not in ("coo", "csr"):
                raise ValueError(
                    "sparse_layout must be 'coo' or 'csr' if mm_function is 'native' or 'tsgu'"
                )
            if mm_function == "native":
                self.mm_function = torch.sparse.mm
            else:
                self.mm_function = tsgu.sparse_mm
        else:
            raise ValueError(f"Invalid mm_function: {mm_function}")

        # Load adjacency matrix if provided
        if adjacency_matrix_path is not None:
            adj = torch.load(adjacency_matrix_path).coalesce()
            indices = adj.indices().long()
            # add identity connection to indices
            if self_recurrence:
                identity = indices.unique().tile(2, 1)
                indices = torch.cat([indices, identity], 1)
            _, inv, fan_in = indices[0].unique(return_inverse=True, return_counts=True)
            scale = torch.sqrt(2 / fan_in.float())
            values = torch.randn(indices.shape[1]) * scale[inv]

        # Create adjacency matrix with normal distribution randomized weights
        else:
            indices = []
            # if initialization == "identity":
            #     values = []
            for i in range(sheet_size[0]):
                for j in range(sheet_size[1]):
                    synapses = (
                        torch.randn(2, synapses_per_neuron) * connectivity_std
                        + torch.tensor((i, j))[:, None]
                    ).long()
                    synapses = torch.cat(
                        [synapses, torch.tensor((i, j))[:, None]], dim=1
                    )
                    synapses = synapses.clamp(
                        torch.tensor((0, 0))[:, None],
                        torch.tensor((sheet_size[0] - 1, sheet_size[1] - 1))[:, None],
                    )
                    synapses = idx_2D_to_1D(synapses, sheet_size[0], sheet_size[1])
                    synapse_root = torch.full_like(
                        synapses,
                        int(
                            idx_2D_to_1D(
                                torch.tensor((i, j)),
                                sheet_size[0],
                                sheet_size[1],
                            )
                        ),
                    )
                    indices.append(torch.stack((synapses, synapse_root)))
                    # if initialization == "identity":
                    #     values.append(
                    #         torch.cat(
                    #             [
                    #                 torch.zeros(synapses_per_neuron),
                    #                 torch.ones(1),
                    #             ]
                    #         )
                    #     )
            indices = torch.cat(indices, dim=1)

            # He initialization of values (synapses_per_neuron is the fan_in)
            # if initialization == "he":
            values = torch.randn(indices.shape[1]) * math.sqrt(2 / synapses_per_neuron)

        self.num_neurons = self.sheet_size[0] * self.sheet_size[1]

        if mm_function == "torch_sparse":
            indices, weight = torch_sparse.coalesce(
                indices, values, self.num_neurons, self.num_neurons
            )
            self.indices = nn.Parameter(indices, requires_grad=False)
        else:
            weight = torch.sparse_coo_tensor(
                indices,
                values,
                (self.num_neurons, self.num_neurons),  # type: ignore
                check_invariants=True,
            ).coalesce()
            if sparse_layout == "csr":
                weight = weight.to_sparse_csr()
        self.weight = nn.Parameter(weight)  # type: ignore

        # Initialize the bias vector
        self.bias = nn.Parameter(torch.zeros(self.num_neurons, 1)) if bias else None

    def coalesce(self):
        """
        Coalesce the weight matrix.
        """
        self.weight.data = self.weight.data.coalesce()

    def forward(self, x):
        """
        Forward pass of the TopographicalCorticalCell.

        Args:
            x (torch.Tensor): Input tensor.

        Returns:
            torch.Tensor: Output tensor.
        """
        # x: Dense (strided) tensor of shape (batch_size, num_neurons) if
        # batch_first, otherwise (num_neurons, batch_size)
        # assert self.weight.is_coalesced()

        # Transpose input if batch_first
        if self.batch_first:
            x = x.t()

        # Perform sparse matrix multiplication with or without bias
        if self.sparse_layout == "torch_sparse":
            x = (
                self.mm_function(
                    self.indices,
                    self.weight,
                    self.num_neurons,  # type: ignore
                    self.num_neurons,
                    x,
                )
                + self.bias
            )
        else:
            x = self.mm_function(self.weight, x) + self.bias

        # Transpose output back to batch first
        if self.batch_first:
            x = x.t()

        return x


class TopographicalRNN(nn.Module):
    def __init__(
        self,
        sheet_size: tuple[int, int] = (150, 300),
        synapse_std: float = 10,
        synapses_per_neuron: int = 32,
        self_recurrence: bool = True,
        connectivity_ih: Optional[str | torch.Tensor] = None,
        connectivity_hh: Optional[str | torch.Tensor] = None,
        sparse_layout: str = "torch_sparse",
        mm_function: str = "torch_sparse",
        num_classes: int = 10,
        batch_first: bool = True,
        input_indices: Optional[str | torch.Tensor] = None,
        output_indices: Optional[str | torch.Tensor] = None,
        nonlinearity: str = "relu",
        bias: bool = True,
    ):
        """
        Initialize the TopographicalCorticalRNN object.

        Args:
            sheet_size (tuple[int, int], optional): The size of the cortical sheet (number of rows, number of columns). Defaults to (256, 256).
            connectivity_std (float, optional): The standard deviation of the connectivity weights. Defaults to 10.
            synapses_per_neuron (int, optional): The number of synapses per neuron. Defaults to 32.
            bias (bool, optional): Whether to include bias in the cortical sheet. Defaults to True.
            mm_function (str, optional): The sparse matrix multiplication function to use in the cortical sheet.
                Possible values are "native", "torch_sparse", and "tsgu". Defaults to "torch_sparse".
            sparse_layout (str, optional): The sparse format to use in the cortical sheet.
                Possible values are "coo", "csr", and "torch_sparse". Defaults to "torch_sparse".
            batch_first (bool, optional): Whether the batch dimension is the first dimension in the cortical sheet. Defaults to True.
            adjacency_matrix_path (str, optional): The path to the adjacency matrix file. Defaults to None.
            self_recurrence (bool, optional): Whether to include self-recurrence in the cortical sheet. Defaults to False.
            num_timesteps (int, optional): The number of timesteps for the recurrent processing. Defaults to 100.
            input_indices (str | torch.Tensor, optional): The input indices for the cortical sheet.
                Can be a path to a .npy or .pt file or a torch.Tensor. Defaults to None.
            output_indices (str | torch.Tensor, optional): The output indices for the cortical sheet.
                Can be a path to a .npy or .pt file or a torch.Tensor. Defaults to None.
            activation (str, optional): The activation function to use. Possible values are "relu" and "gelu". Defaults to "relu".
            initialization (str, optional): The initialization method for the cortical sheet. Defaults to "identity".
        """
        super().__init__()

        self.batch_first = batch_first
        self.nonlinearity = get_activation_class(nonlinearity)()

        use_random = synapse_std is not None and synapses_per_neuron is not None
        use_connectivity = connectivity_hh is not None and connectivity_ih is not None
        if use_connectivity:
            if use_random:
                warn(
                    "Both random initialization and connectivity initialization are provided. Using connectivity initialization."
                )
                use_random = False
            try:
                connectivity_ih = torch.load(connectivity_ih)
                connectivity_hh = torch.load(connectivity_hh)
            except Exception:
                pass

            if connectivity_ih.layout != "coo" or connectivity_hh.layout != "coo":
                raise ValueError("Connectivity matrices must be in COO format")
            if (
                connectivity_ih.shape[0]
                != connectivity_ih.shape[1]
                != connectivity_hh.shape[0]
                != connectivity_hh.shape[1]
            ):
                raise ValueError("Connectivity matrices must be square")

            num_neurons = connectivity_ih.shape[0]

        elif use_random:
            num_neurons = math.prod(sheet_size)

            idx_1d = torch.arange(num_neurons)
            idx = idx_1D_to_2D(idx_1d, sheet_size[0], sheet_size[1]).t()
            synapses = (
                torch.randn(num_neurons, 2, synapses_per_neuron) * synapse_std
                + idx.unsqueeze(-1)
            ).long()
            if self_recurrence:
                synapses = torch.cat([synapses, idx.unsqueeze(-1)], dim=2)
            synapses = synapses.clamp(
                torch.zeros(2).view(1, 2, 1),
                torch.tensor((sheet_size[0] - 1, sheet_size[1] - 1)).view(1, 2, 1),
            )
            synapses = idx_2D_to_1D(
                synapses.transpose(0, 1).flatten(1), sheet_size[0], sheet_size[1]
            ).view(num_neurons, -1)

            synapse_root = idx_1d.expand(-1, synapses_per_neuron + 1)

            indices = torch.stack((synapses, synapse_root)).flatten(1)

            # He initialization of values (synapses_per_neuron is the fan_in)
            values_ih = torch.randn(indices.shape[1]) * math.sqrt(
                2 / synapses_per_neuron
            )
            values_hh = torch.randn(indices.shape[1]) * math.sqrt(
                2 / synapses_per_neuron
            )

            connectivity_ih = torch.sparse_coo_tensor(
                indices,
                values_ih,
                (math.prod(sheet_size), math.prod(sheet_size)),  # type: ignore
                check_invariants=True,
            ).coalesce()

            connectivity_hh = torch.sparse_coo_tensor(
                indices,
                values_hh,
                (math.prod(sheet_size), math.prod(sheet_size)),  # type: ignore
                check_invariants=True,
            ).coalesce()
        else:
            raise ValueError(
                "Either connectivity or random initialization must be provided"
            )

        try:
            try:
                input_indices = torch.load(input_indices)
            except Exception:
                input_indices = np.load(input_indices)
                input_indices = torch.tensor(input_indices)
        except Exception:
            pass

        try:
            try:
                output_indices = torch.load(output_indices)
            except Exception:
                output_indices = np.load(output_indices)
                output_indices = torch.tensor(output_indices)
        except Exception:
            pass

        if (input_indices is not None and input_indices.dim() > 1) or (
            output_indices is not None and output_indices.dim() > 1
        ):
            raise ValueError("Input and output indices must be 1D tensors")

        self.input_indices = input_indices
        self.output_indices = output_indices

        # Create the CorticalSheet layer
        self.rnn = SparseRNN(
            num_neurons,
            num_neurons,
            connectivity_ih,
            connectivity_hh,
            num_layers=1,
            sparse_layout=sparse_layout,
            mm_function=mm_function,
            batch_first=batch_first,
            nonlinearity=nonlinearity,
            bias=bias,
        )
        num_out_neurons = (
            num_neurons if output_indices is None else output_indices.shape[0]
        )

        # Create output block
        self.out_block = nn.Sequential(
            nn.Linear(num_out_neurons, 64),
            self.activation,
            nn.Linear(64, num_classes),
        )

    def visualize(self, activations, save_path=None, fps=4, frames=None):
        """
        Visualize the forward pass of the TopographicalCorticalRNN.

        Args:
            x (torch.Tensor): Input tensor of size (batch_size, num_neurons) or (batch_size, num_channels, num_neurons).

        Returns:
            torch.Tensor: Output tensor.
        """
        if frames is not None:
            activations = activations[frames[0] : frames[1]]
        for i in range(len(activations)):
            activations[i] = activations[i][0].reshape(*self.cortical_sheet.sheet_size)

        # First set up the figure, the axis, and the plot element we want to animate
        fig = plt.figure(figsize=(8, 8))

        im = plt.imshow(
            activations[0], interpolation="none", aspect="auto", vmin=0, vmax=1
        )

        def animate_func(i):
            if i % fps == 0:
                print(".", end="")

            im.set_array(activations[i])
            return [im]

        anim = animation.FuncAnimation(
            fig,
            animate_func,
            frames=len(activations),
            interval=1000 / fps,  # in ms
        )

        if save_path is not None:
            anim.save(
                save_path,
                fps=fps,
            )
        else:
            plt.show()

    def forward(
        self,
        x,
        num_steps=None,
        return_activations=False,
    ):
        """
        Forward pass of the TopographicalCorticalRNN.

        Args:
            x (torch.Tensor): Input tensor of size (batch_size, num_neurons) or (batch_size, num_channels, num_neurons).

        Returns:
            torch.Tensor: Output tensor.
        """

        # Average out channel dimension if it exists
        if x.dim() > 2:
            x = x.flatten(2)
            x = x.mean(dim=1)

        if self.input_indices is not None:
            input_x = torch.zeros(
                x.shape[0],
                self.cortical_sheet.num_neurons,
                device=x.device,
                dtype=x.dtype,
            )
            input_x[:, self.input_indices] = x
            x = input_x

        # To avoid tranposing x before and after every iteration, we tranpose
        # before and after ALL iterations and do not tranpose within forward()
        # of self.cortical_sheet
        if self.batch_first:
            x = x.t()

        input_x = x

        # Pass the input through the CorticalSheet layer num_timesteps times
        if return_activations:
            activations = [x.t().detach().cpu()]

        for _ in range(self.num_timesteps):
            x = self.activation(self.cortical_sheet(input_x + x))
            if return_activations:
                activations.append(x.t().detach().cpu())

        # Transpose back
        if self.batch_first:
            x = x.t()

        # Select output indices if provided
        if self.output_indices is not None:
            x = x[:, self.output_indices]

        # Return classification from out_block
        if return_activations:
            activations = torch.stack(activations)
            return self.out_block(x), activations
        else:
            return self.out_block(x)


if __name__ == "__main__":
    import os

    dir_path = os.path.dirname(os.path.realpath(__file__))
    with open(os.path.join(dir_path, "topography_trainer.py")) as file:
        exec(file.read())
