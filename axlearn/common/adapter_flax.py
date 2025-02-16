# Copyright © 2023 Apple Inc.

"""Adapter layers to use Flax/Linen modules.

FlaxLayer allows users to use flax.linen modules in an AXLearn module hierarchy.
See the FeedForward layer in adapter_flax_test.py for an example.
"""
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import jax.random
from flax.linen import Module as FlaxModule

from axlearn.common import utils
from axlearn.common.base_layer import BaseLayer, NestedParameterSpec, ParameterSpec, PartitionSpec
from axlearn.common.config import REQUIRED, Required, config_class
from axlearn.common.module import Module, NestedTensor


class FlaxLayer(BaseLayer):
    """Base Flax adapter layer."""

    @config_class
    class Config(BaseLayer.Config):
        """Configures FlaxLayer."""

        # A function to return a linen.Module.
        create_module_fn: Required[Callable[[], FlaxModule]] = REQUIRED
        create_module_kwargs: Dict[str, Any] = {}  # The kwargs for create_module_fn.

        # A function to return (args, kwargs) used for linen.Module.init.
        create_dummy_input_fn: Required[Callable[[], Tuple[Sequence, Dict]]] = REQUIRED
        create_dummy_input_kwargs: Dict[str, Any] = {}  # The kwargs for create_dummy_input_fn.

    def __init__(self, cfg: Config, *, parent: Module):
        super().__init__(cfg, parent=parent)
        self._module = self._create_flax_module()
        self.vlog(1, "module=%s", self._module)
        self._dummy_inputs = self._create_dummy_inputs()
        self.vlog(1, "dummy_inputs=%s", utils.shapes(self._dummy_inputs))

    def create_parameter_specs_recursively(self) -> NestedParameterSpec:
        # Create parameters with a dummy PRNGKey. The parameters are used only to generate a
        # NestedParameterSpec.
        params = self.initialize_parameters_recursively(jax.random.PRNGKey(0))
        if self.config.param_partition_spec is not None:
            raise NotImplementedError("FlaxLayer does not support partitioned parameters yet.")
        return jax.tree_util.tree_map(
            lambda x: ParameterSpec(
                dtype=x.dtype,
                shape=x.shape,
                # Replicate the parameter.
                mesh_axes=PartitionSpec(*([None] * len(x.shape))),
            ),
            params,
        )

    def _create_flax_module(self) -> FlaxModule:
        cfg = self.config
        return cfg.create_module_fn(**cfg.create_module_kwargs)

    def _create_dummy_inputs(self):
        cfg = self.config
        return cfg.create_dummy_input_fn(**cfg.create_dummy_input_kwargs)

    def initialize_parameters_recursively(
        self, prng_key: jax.random.KeyArray, *, prebuilt: Optional[NestedTensor] = None
    ) -> NestedTensor:
        if self._use_prebuilt_params(prebuilt):
            return prebuilt
        args, kwargs = self._dummy_inputs
        return self._module.init(prng_key, *args, **kwargs)

    def forward(
        self,
        *args,
        rngs: Optional[Dict[str, jax.random.PRNGKey]] = None,
        mutable: Optional[Union[bool, str, List[str]]] = None,
        module_method: Optional[str] = None,
        **kwargs,
    ):
        method = getattr(self._module, module_method or "__call__")
        if mutable is None:
            mutable = "batch_stats" if self.is_training else False
        apply_outputs = self._module.apply(
            self.parameters,
            *args,
            rngs=rngs,
            mutable=mutable,
            method=method,
            **kwargs,
        )
        if mutable:
            outputs, variable_updates = apply_outputs
            self.vlog(3, "variable_updates=%s", variable_updates)
            for name, value in variable_updates.items():
                self.add_state_update(name, value)
        else:
            outputs = apply_outputs
        return outputs


def config_for_flax_module(
    create_module_fn: Callable[[], FlaxModule],
    create_dummy_input_fn: Callable[[], NestedTensor],
    **kwargs,
):
    return FlaxLayer.default_config().set(
        create_module_fn=create_module_fn, create_dummy_input_fn=create_dummy_input_fn, **kwargs
    )
