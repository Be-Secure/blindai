# Copyright 2022 Mithril Security. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
## <kbd>Enum</kbd> `ModelDatumType` : An enumeration of the acceptable input data types.

Used to specify the type of the input and output data of a model before uploading it to the server.

Supported types :

    * ModelDatumType::F32 ---> float32

    * ModelDatumType::F64 ---> float64

    * ModelDatumType::I32 ---> int32

    * ModelDatumType::I64 ---> int64

    * ModelDatumType::U32 ---> unsigned int 32

    * ModelDatumType::U64 ---> unsigned int 64
"""

import contextlib
from functools import wraps
import getpass
import logging
import os
import socket
import ssl
import platform
from hashlib import sha256
from typing import Any, List, Optional, Tuple, Union

from cryptography.exceptions import InvalidSignature
from grpc import Channel, RpcError, secure_channel, ssl_channel_credentials
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from blindai.dcap_attestation import (
    Policy,
    verify_claims,
    verify_dcap_attestation,
)

# These modules are generated by grpc proto compiler, from proto files in proto
import blindai.pb as _
from blindai.pb.securedexchange_pb2 import (
    Payload as PbPayload,
    RunModelRequest as PbRunModelRequest,
    RunModelReply as PbRunModelReply,
    SendModelRequest as PbSendModelRequest,
    ClientInfo,
    TensorInfo as PbTensorInfo,
    DeleteModelRequest as PbDeleteModelRequest,
    TensorData as PbTensorData,
)
import grpc
import blindai.pb.licensing_pb2 as licensing_pb2
import blindai.pb.licensing_pb2_grpc as licensing_pb2_grpc
from blindai.pb.proof_files_pb2 import ResponseProof
from blindai.pb.securedexchange_pb2_grpc import ExchangeStub
from blindai.pb.untrusted_pb2 import GetCertificateRequest as certificate_request
from blindai.pb.untrusted_pb2 import GetServerInfoRequest as server_info_request
from blindai.pb.untrusted_pb2 import GetSgxQuoteWithCollateralReply
from blindai.pb.untrusted_pb2 import GetSgxQuoteWithCollateralRequest as quote_request
from blindai.pb.untrusted_pb2_grpc import AttestationStub
from blindai.utils.errors import (
    SignatureError,
    VersionError,
    check_rpc_exception,
    check_socket_exception,
)
from blindai.utils.utils import (
    create_byte_chunk,
    encode_certificate,
    get_enclave_signing_key,
    strip_https,
    supported_server_version,
    ModelDatumType,
)
from blindai.version import __version__ as app_version

from blindai.utils.serialize import deserialize_tensor, serialize_tensor

dt_per_type = {
    ModelDatumType.F32: 4,
    ModelDatumType.F64: 8,
    ModelDatumType.I32: 4,
    ModelDatumType.I64: 8,
    ModelDatumType.U32: 4,
    ModelDatumType.U64: 8,
    ModelDatumType.U8: 1,
    ModelDatumType.U16: 2,
    ModelDatumType.I8: 1,
    ModelDatumType.I16: 2,
    ModelDatumType.Bool: 1,
}

CONNECTION_TIMEOUT = 10

JWT="not yet defined"
URL="not yet defined"


def is_torch_installed():
    try:
        import torch

        return True
    except:
        return False


def convert_dt(tensor):
    import torch

    if isinstance(tensor[0], float) or tensor[0][0].dtype == torch.float32:
        return ModelDatumType.F32
    elif tensor[0][0].dtype == torch.float64:
        return ModelDatumType.F64
    if isinstance(tensor[0], int) or tensor[0][0].dtype == torch.int32:
        return ModelDatumType.I32
    if tensor[0][0].dtype == torch.int64:
        return ModelDatumType.I64
    if tensor[0][0].dtype == torch.uint8:
        return ModelDatumType.U8
    if tensor[0][0].dtype == torch.int16:
        return ModelDatumType.I16


def _validate_quote(
    attestation: GetSgxQuoteWithCollateralReply, policy: Policy
) -> Ed25519PublicKey:
    """Returns the enclave signing key"""

    claims = verify_dcap_attestation(
        attestation.quote, attestation.collateral, attestation.enclave_held_data
    )

    verify_claims(claims, policy)
    server_cert = claims.get_server_cert()
    enclave_signing_key = get_enclave_signing_key(server_cert)

    return enclave_signing_key


def _get_input_output_tensors(
    tensor_inputs: Optional[List[List[Any]]],
    tensor_outputs: Optional[ModelDatumType],
    shape: Tuple,
    datum_type: ModelDatumType,
    dtype_out: ModelDatumType,
) -> Tuple[List[List[Any]], List[ModelDatumType]]:
    if tensor_inputs is None or tensor_outputs is None:
        tensor_inputs = [shape, datum_type]
        tensor_outputs = [dtype_out]

    if type(tensor_inputs[0]) != list:
        tensor_inputs = [tensor_inputs]
    if type(tensor_outputs[0]) != list:
        tensor_outputs = [tensor_outputs]

    inputs = []
    for i, tensor_input in enumerate(tensor_inputs):
        inputs.append(
            PbTensorInfo(dims=tensor_input[0], datum_type=tensor_input[1], index=i)
        )

    outputs = []
    for i, tensor_output in enumerate(tensor_outputs):
        outputs.append(PbTensorInfo(datum_type=tensor_output[0], index=i))

    return (inputs, outputs)


class SignedResponse:
    payload: Optional[bytes] = None
    signature: Optional[bytes] = None
    attestation: Optional[GetSgxQuoteWithCollateralReply] = None

    def is_simulation_mode(self) -> bool:
        return self.attestation is None

    def is_signed(self) -> bool:
        return self.signature is not None

    def save_to_file(self, path: str):
        """Save the response to a file.
        The response can later be loaded with:

        ```py
        res = SignedResponse()
        res.load_from_file(path)
        ```

        Args:
            path (str): Path of the file.
        """
        with open(path, mode="wb") as file:
            file.write(self.as_bytes())

    def as_bytes(self) -> bytes:
        """Save the response as bytes.
        The response can later be loaded with:

        ```py
        res = SignedResponse()
        res.load_from_bytes(data)
        ```

        Returns:
            bytes: The data.
        """
        return ResponseProof(
            payload=self.payload,
            signature=self.signature,
            attestation=self.attestation,
        ).SerializeToString()

    def load_from_file(self, path: str):
        """Load the response from a file.

        Args:
            path (str): Path of the file.
        """
        with open(path, "rb") as file:
            self.load_from_bytes(file.read())

    def load_from_bytes(self, b: bytes):
        """Load the response from bytes.

        Args:
            b (bytes): The data.
        """
        proof = ResponseProof.FromString(b)
        self.payload = proof.payload
        self.signature = proof.signature
        self.attestation = proof.attestation
        self._load_payload()

    def _load_payload(self):
        pass


class UploadModelResponse(SignedResponse):
    model_id: str

    def validate(
        self,
        model_hash: bytes,
        policy_file: Optional[str] = None,
        policy: Optional[Policy] = None,
        validate_quote: bool = True,
        enclave_signing_key: Optional[bytes] = None,
        allow_simulation_mode: bool = False,
    ):
        """Validates whether this response is valid. This is used for responses you have saved as bytes or in a file.
        This will raise an error if the response is not signed or if it is not valid.

        Args:
            model_hash (bytes): Hash of the model to verify against.
            policy_file (Optional[str], optional): Path to the policy file. Defaults to None.
            policy (Optional[Policy], optional): Policy to use. Use `policy_file` to load from a file directly. Defaults to None.
            validate_quote (bool, optional): Whether or not the attestation should be validated too. Defaults to True.
            enclave_signing_key (Optional[bytes], optional): Enclave signing key in case the attestation should not be validated. Defaults to None.
            allow_simulation_mode (bool, optional): Whether or not simulation mode responses should be accepted. Defaults to False.

        Raises:
            AttestationError: Attestation is invalid.
            SignatureError: Signed response is invalid.
            FileNotFoundError: Will be raised if the policy file is not found.
        """
        if not self.is_signed():
            raise SignatureError("Response is not signed")

        if not allow_simulation_mode and self.is_simulation_mode():
            raise SignatureError("Response was produced using simulation mode")

        if not self.is_simulation_mode() and validate_quote and policy_file is not None:
            policy = Policy.from_file(policy_file)

        # Quote validation

        if not self.is_simulation_mode() and validate_quote:
            enclave_signing_key = _validate_quote(self.attestation, policy)

        # Payload validation

        payload = PbPayload.FromString(self.payload).send_model_payload
        if not self.is_simulation_mode():
            try:
                enclave_signing_key.verify(self.signature, self.payload)
            except InvalidSignature:
                raise SignatureError("Invalid signature")

        # Input validation

        if model_hash != payload.model_hash:
            raise SignatureError("Invalid returned model_hash")

    def _load_payload(self):
        payload = PbPayload.FromString(self.payload).send_model_payload
        self.model_id = payload.model_id


class TensorInfo:
    dims: List[int]
    datum_type: ModelDatumType
    index: int
    index_name: str

    def __init__(
        self, dims: List[int], datum_type: ModelDatumType, index: int, index_name: str
    ):
        self.dims = dims
        self.datum_type = datum_type


class Tensor:
    info: TensorInfo
    bytes_data: bytes

    def __init__(self, info: TensorInfo, bytes_data: bytes):
        self.info = info
        self.bytes_data = bytes_data

    def as_flat(self) -> list:
        return list(deserialize_tensor(self.bytes_data, self.info.datum_type))

    @property
    def shape(self) -> tuple:
        return tuple(self.info.dims)

    @property
    def datum_type(self) -> ModelDatumType:
        return self.info.datum_type


class RunModelResponse(SignedResponse):
    output_tensors: List[Tensor]
    model_id: str

    def __init__(
        self,
        input_tensors: Union[List[List[Any]], List[Any]],
        input_datum_type: Union[List[ModelDatumType], ModelDatumType],
        input_shape: Union[List[List[int]], List[int]],
        response: PbRunModelReply,
        sign: bool,
        attestation: Optional[GetSgxQuoteWithCollateralReply] = None,
        enclave_signing_key: Optional[bytes] = None,
        allow_simulation_mode: bool = False,
    ):
        payload = PbPayload.FromString(response.payload).run_model_payload
        self.output_tensors = [
            Tensor(
                info=TensorInfo(
                    tensor.info.dims,
                    tensor.info.datum_type,
                    tensor.info.index,
                    tensor.info.index_name,
                ),
                bytes_data=tensor.bytes_data,
            )
            for tensor in payload.output_tensors
        ]
        self.model_id = payload.model_id

        # Response Verification
        if sign:
            self.payload = response.payload
            self.signature = response.signature
            self.attestation = attestation
            self.validate(
                self.model_id,
                input_tensors,
                input_datum_type,
                input_shape,
                validate_quote=False,
                enclave_signing_key=enclave_signing_key,
                allow_simulation_mode=allow_simulation_mode,
            )

    def validate(
        self,
        model_id: str,
        tensors: Union[List[List[Any]], List[Any]],
        dtype: Union[List[ModelDatumType], ModelDatumType],
        shape: Union[List[List[int]], List[int]],
        policy_file: Optional[str] = None,
        policy: Optional[Policy] = None,
        validate_quote: bool = True,
        enclave_signing_key: Optional[bytes] = None,
        allow_simulation_mode: bool = False,
    ):
        """Validates whether this response is valid. This is used for responses you have saved as bytes or in a file.
        This will raise an error if the response is not signed or if it is not valid.

        Args:
            model_id (str): The model id to check against.
            tensors (List[Any]): Input used to run the model, to validate against.
            policy_file (Optional[str], optional): Path to the policy file. Defaults to None.
            policy (Optional[Policy], optional): Policy to use. Use `policy_file` to load from a file directly. Defaults to None.
            validate_quote (bool, optional): Whether or not the attestation should be validated too. Defaults to True.
            enclave_signing_key (Optional[bytes], optional): Enclave signing key in case the attestation should not be validated. Defaults to None.
            allow_simulation_mode (bool, optional): Whether or not simulation mode responses should be accepted. Defaults to False.

        Raises:
            AttestationError: Attestation is invalid.
            SignatureError: Signed response is invalid.
            FileNotFoundError: Will be raised if the policy file is not found.
        """
        if not self.is_signed():
            raise SignatureError("Response is not signed")

        if not allow_simulation_mode and self.is_simulation_mode():
            raise SignatureError("Response was produced using simulation mode")

        if not self.is_simulation_mode() and validate_quote and policy_file is not None:
            policy = Policy.from_file(policy_file)

        # Quote validation

        if not self.is_simulation_mode() and validate_quote:
            enclave_signing_key = _validate_quote(self.attestation, policy)

        # Payload validation

        payload = PbPayload.FromString(self.payload).run_model_payload
        if not self.is_simulation_mode():
            try:
                enclave_signing_key.verify(self.signature, self.payload)
            except InvalidSignature:
                raise SignatureError("Invalid signature")

        # Input validation
        if type(tensors[0]) != list:
            tensors = [tensors]
        if type(dtype) != list:
            dtype = [dtype]
        if type(shape[0]) != list:
            shape = [shape]

        hash = sha256()
        for i, tensor in enumerate(tensors):
            for chunk in serialize_tensor(tensor, dtype[i]):
                hash.update(chunk)

        if hash.digest() != payload.input_hash:
            raise SignatureError("Invalid returned input_hash")

        if model_id != payload.model_id:
            raise SignatureError("Invalid returned model_id")


class DeleteModelResponse:
    pass


def raise_exception_if_conn_closed(f):
    """
    Decorator which raises an exception if the BlindAiConnection is closed before calling
    the decorated method
    """

    @wraps(f)
    def wrapper(self, *args, **kwds):
        if self.closed:
            raise ValueError("Illegal operation on closed connection.")
        return f(self, *args, **kwds)

    return wrapper


class BlindAiConnection(contextlib.AbstractContextManager):
    _channel: Optional[Channel] = None
    policy: Optional[Policy] = None
    _stub: Optional[ExchangeStub] = None
    enclave_signing_key: Optional[bytes] = None
    simulation_mode: bool = False
    _disable_untrusted_server_cert_check: bool = False
    attestation: Optional[GetSgxQuoteWithCollateralReply] = None
    server_version: Optional[str] = None
    client_info: ClientInfo
    tensor_inputs: Optional[List[List[Any]]]
    tensor_outputs: Optional[List[ModelDatumType]]
    closed: bool = False

    def __init__(
        self,
        addr: str,
        server_name: str = "blindai-srv",
        policy: Optional[str] = None,
        certificate: Optional[str] = None,
        simulation: bool = False,
        untrusted_port: int = 50052,
        attested_port: int = 50051,
        debug_mode=False,
        #api_key: str = None
    ):
        """Connect to the server with the specified parameters.
        You will have to specify here the expected policy (server identity, configuration...)
        and the server TLS certificate, if you are using the hardware mode.

        If you're using the simulation mode, you don't need to provide a policy and certificate,
        but please keep in mind that this mode should NEVER be used in production as it doesn't
        have most of the security provided by the hardware mode.

        Args:
            addr (str): The address of BlindAI server you want to reach.
            server_name (str, optional): Contains the CN expected by the server TLS certificate. Defaults to "blindai-srv".
            policy (Optional[str], optional): Path to the toml file describing the policy of the server.
                Generated in the server side. Defaults to None.
            certificate (Optional[str], optional): Path to the public key of the untrusted inference server.
                Generated in the server side. Defaults to None.
            simulation (bool, optional): Connect to the server in simulation mode.
                If set to True, the args policy and certificate will be ignored. Defaults to False.
            untrusted_port (int, optional): Untrusted connection server port. Defaults to 50052.
            attested_port (int, optional): Attested connection server port. Defaults to 50051.

        Raises:
            AttestationError: Will be raised in case the policy doesn't match the
                server identity and configuration, or if te attestation is invalid.
            ConnectionError: will be raised if the connection with the server fails.
            VersionError: Will be raised if the version of the server is not supported by the client.
            FileNotFoundError: will be raised if the policy file, or the certificate file is not
                found (in Hardware mode).
        """
        if debug_mode:  # pragma: no cover
            os.environ["GRPC_TRACE"] = "transport_security,tsi"
            os.environ["GRPC_VERBOSITY"] = "DEBUG"

        """
/*****************************************************************************************************
 * ***************************************************************************************************
 * BEGINNING gRPC part
 *****************************************************************************************************
******************************************************************************************************/

        #open and connect to the gRPC channel we just created
        channel = grpc.insecure_channel('localhost:8080')
        #create a stub (client)
        stub = licensing_pb2_grpc.LicensingServiceStub(channel)
        api_key_pb = licensing_pb2.GetEnclaveRequest(api_key=api_key)
        #make the call
        # response = stub.GetEnclave(api_key_pb)
        # #print the results
        # print(response)
        variable=globals()
        variable["JWT"]=response.jwt
        variable["URL"]=response.enclave_url

/*****************************************************************************************************
 *****************************************************************************************************
 * END gRPC part
 *****************************************************************************************************
******************************************************************************************************/
    """        
        variable=globals()
        variable["JWT"]="JWT"
        variable["URL"]="URL"

        uname = platform.uname()
        self.client_info = ClientInfo(
            uid=sha256((socket.gethostname() + "-" + getpass.getuser()).encode("utf-8"))
            .digest()
            .hex(),
            platform_name=uname.system,
            platform_arch=uname.machine,
            platform_version=uname.version,
            platform_release=uname.release,
            user_agent="blindai_python",
            user_agent_version=app_version,
        )

        self._connect_server(
            addr,
            server_name,
            policy,
            certificate,
            simulation,
            untrusted_port,
            attested_port,
        )

    def _connect_server(
        self,
        addr: str,
        server_name,
        policy,
        certificate,
        simulation,
        untrusted_port,
        attested_port,
    ):
        self.simulation_mode = simulation
        self._disable_untrusted_server_cert_check = simulation

        addr = strip_https(addr)

        untrusted_client_to_enclave = addr + ":" + str(untrusted_port)
        attested_client_to_enclave = addr + ":" + str(attested_port)

        if not self.simulation_mode:
            self.policy = Policy.from_file(policy)

        if self._disable_untrusted_server_cert_check:
            logging.warning("Untrusted server certificate check bypassed")

            try:
                socket.setdefaulttimeout(CONNECTION_TIMEOUT)
                untrusted_server_cert = ssl.get_server_certificate(
                    (addr, untrusted_port)
                )
                untrusted_server_creds = ssl_channel_credentials(
                    root_certificates=bytes(untrusted_server_cert, encoding="utf8")
                )

            except RpcError as rpc_error:
                raise ConnectionError(check_rpc_exception(rpc_error))

            except socket.error as socket_error:
                raise ConnectionError(check_socket_exception(socket_error))

        else:
            with open(certificate, "rb") as f:
                untrusted_server_creds = ssl_channel_credentials(
                    root_certificates=f.read()
                )

        connection_options = (("grpc.ssl_target_name_override", server_name),)

        try:
            channel = secure_channel(
                untrusted_client_to_enclave,
                untrusted_server_creds,
                options=connection_options,
            )
            stub = AttestationStub(channel)

            response = stub.GetServerInfo(server_info_request())
            self.server_version = response.version
            if not supported_server_version(response.version):
                raise VersionError(
                    "Incompatible client/server versions. Please use the correct client for your server."
                )

            if self.simulation_mode:
                logging.warning(
                    "Attestation process is bypassed: running without requesting and checking attestation"
                )
                response = stub.GetCertificate(certificate_request())
                server_cert = encode_certificate(response.enclave_tls_certificate)

            else:
                self.attestation = stub.GetSgxQuoteWithCollateral(quote_request())
                claims = verify_dcap_attestation(
                    self.attestation.quote,
                    self.attestation.collateral,
                    self.attestation.enclave_held_data,
                )

                verify_claims(claims, self.policy)
                server_cert = claims.get_server_cert()

                logging.info("Quote verification passed")
                logging.info(
                    f"Certificate from attestation process\n {server_cert.decode('ascii')}"
                )
                logging.info("MREnclave\n" + claims.sgx_mrenclave)

            channel.close()
            self.enclave_signing_key = get_enclave_signing_key(server_cert)
            server_creds = ssl_channel_credentials(root_certificates=server_cert)
            channel = secure_channel(
                attested_client_to_enclave, server_creds, options=connection_options
            )

            self._stub = ExchangeStub(channel)
            self._channel = channel
            logging.info("Successfuly connected to the server")

        except RpcError as rpc_error:
            channel.close()
            raise ConnectionError(check_rpc_exception(rpc_error))

    @raise_exception_if_conn_closed
    def upload_model(
        self,
        model: str,
        tensor_inputs: Optional[List[Tuple[List[int], ModelDatumType]]] = None,
        tensor_outputs: Optional[List[ModelDatumType]] = None,
        shape: Tuple = None,
        dtype: ModelDatumType = None,
        dtype_out: ModelDatumType = None,
        sign: bool = False,
        model_name: Optional[str] = None,
        save_model: bool = False,
        model_id: Optional[str] = None,
    ) -> UploadModelResponse:
        """Upload an inference model to the server.
        The provided model needs to be in the Onnx format.

        Args:
            model (str): Path to Onnx model file.
            tensor_inputs (List[Tuple[List[int], ModelDatumType]], optional): The list of input fact and datum types for each input grouped together in lists, describing the different inputs of the model. Defaults to None.
            tensor_outputs (List[ModelDatumType], optional): The list of datum types describing the different output types of the model. Defaults to ModelDatumType.F32
            shape (Tuple, optional): The shape of the model input. Defaults to None.
            datum_type (ModelDatumType, optional): The type of the model input data (f32 by default). Defaults to ModelDatumType.F32.
            dtype_out (ModelDatumType, optional): The type of the model output data (f32 by default). Defaults to ModelDatumType.F32.
            sign (bool, optional): Get signed responses from the server or not. Defaults to False.
            model_name (Optional[str], optional): Name of the model.
            save_model (bool, optional): Whether or not the model will be saved to disk in the server. The model will be saved encrypted (sealed) so that only the server enclave can load it afterwards. The server will load the model on startup. Defaults to False.
            model_id (Optional[str], optional): Id of the model. By default, the server will assign a random UUID.

        Raises:
            ConnectionError: Will be raised if the client is not connected.
            FileNotFoundError: Will be raised if the model file is not found.
            SignatureError: Will be raised if the response signature is invalid.
            ValueError: Will be raised if the connection is closed.

        Returns:
            UploadModelResponse: The response object.
        """
        response = None

        if model_name is None:
            model_name = os.path.basename(model)
        try:
            with open(model, "rb") as f:
                data = f.read()

            (inputs, outputs) = _get_input_output_tensors(
                tensor_inputs, tensor_outputs, shape, dtype, dtype_out
            )
            response = self._stub.SendModel(
                (
                    PbSendModelRequest(
                        length=len(data),
                        data=chunk,
                        sign=sign,
                        model_id=model_id,
                        model_name=model_name,
                        client_info=self.client_info,
                        tensor_inputs=inputs,
                        tensor_outputs=outputs,
                        save_model=save_model,
                        jwt=JWT
                    )
                    for chunk in create_byte_chunk(data)
                )
            )

        except RpcError as rpc_error:
            raise ConnectionError(check_rpc_exception(rpc_error))

        # Response Verification
        payload = PbPayload.FromString(response.payload).send_model_payload
        ret = UploadModelResponse()
        ret.model_id = payload.model_id

        if sign:
            ret.payload = response.payload
            ret.signature = response.signature
            ret.attestation = self.attestation
            ret.validate(
                sha256(data).digest(),
                validate_quote=False,
                enclave_signing_key=self.enclave_signing_key,
                allow_simulation_mode=self.simulation_mode,
            )

        return ret

    @raise_exception_if_conn_closed
    def run_model(
        self,
        model_id: str,
        tensors: Union[List[List[Any]], List[Any]],
        dtype: Optional[Union[List[ModelDatumType], ModelDatumType]] = None,
        shape: Optional[Union[List[List[int]], List[int]]] = None,
        sign: bool = False,
    ) -> RunModelResponse:
        """Send data to the server to make a secure inference.
        The data provided must be in a list, as the tensor will be rebuilt inside the server.

        Args:
            model_id (str): If set, will run a specific model.
            tensors (Union[List[Any], List[List[Any]]))): The input data. It must be an array of numbers or an array of arrays of numbers of the same type datum_type specified in `upload_model`.
            sign (bool, optional): Get signed responses from the server or not. Defaults to False.
        Raises:
            ConnectionError: Will be raised if the client is not connected.
            SignatureError: Will be raised if the response signature is invalid
            ValueError: Will be raised if the connection is closed
        Returns:
            RunModelResponse: The response object.
        """
        if is_torch_installed():
            import torch

        try:
            if (
                type(tensors[0]) != list
                and isinstance(tensors[0], torch.Tensor) == False
            ):
                tensors = [tensors]
                if type(dtype) != list:
                    dtype = [dtype]
                if type(shape[0]) != list:
                    shape = [shape]

            elif isinstance(tensors, torch.Tensor):
                dtype = [convert_dt(tensors)]
                shape = [tensors.shape]
                tensors = torch.flatten(tensors, start_dim=1)

            elif isinstance(tensors, list) and isinstance(tensors[0], torch.Tensor):
                dtype = []
                shape = []
                for i in range(len(tensors)):
                    dtype.append(convert_dt(tensors[i]))
                    shape.append(tensors[i].shape)
                    tensors[i] = torch.flatten(tensors[i], start_dim=1)[0]

            """
            for i, tensor in enumerate(tensors):
                print(len(tensor))
                print(tensor) """

            response = self._stub.RunModel(
                (
                    # this will create an iterator of PbRunModelRequest, that will return
                    # chunks, one tensor input at a time
                    PbRunModelRequest(
                        model_id=model_id,
                        client_info=self.client_info,
                        input_tensors=[
                            PbTensorData(
                                info=PbTensorInfo(
                                    dims=shape[i],
                                    datum_type=dtype[i],
                                    index=i,  # only send the ith tensor for this call
                                ),
                                bytes_data=chunk,
                            )
                        ],
                        sign=sign,
                    )
                    for i, tensor in enumerate(tensors)
                    for chunk in serialize_tensor(tensor, dtype[i])
                )
            )

        except RpcError as rpc_error:
            raise ConnectionError(check_rpc_exception(rpc_error))

        return RunModelResponse(
            tensors,
            dtype,
            shape,
            response,
            sign,
            self.attestation,
            self.enclave_signing_key,
            self.simulation_mode,
        )

    @raise_exception_if_conn_closed
    def delete_model(self, model_id: str) -> DeleteModelResponse:
        """Delete a model in the inference server.
        This may be used to free up some memory.
        Note that the model in currently stored in-memory, and you cannot keep it loaded across server restarts.

        Args:
            model_id (str): The id of the model to remove.

        Raises:
            ConnectionError: Will be raised if the client is not connected or if an happens.
            ValueError: Will be raised if the connection is closed
        Returns:
            DeleteModelResponse: The response object.
        """
        try:
            self._stub.DeleteModel(PbDeleteModelRequest(model_id=model_id))

        except RpcError as rpc_error:
            raise ConnectionError(check_rpc_exception(rpc_error))

        return DeleteModelResponse()

    def close(self):
        """Close the connection between the client and the inference server. This method has no effect if the file is already closed."""
        if not self.closed:
            self._channel.close()
            self.closed = True
            self._channel = None
            self._stub = None
            self.policy = None
            self.server_version = None

    def __enter__(self):
        """Return the BlindAiConnection upon entering the runtime context."""
        return self

    def __exit__(self, *args):
        """Close the connection to BlindAI server and raise any exception triggered within the runtime context."""
        self.close()


@wraps(BlindAiConnection.__init__, assigned=("__doc__", "__annotations__"))
def connect(*args, **kwargs):
    return BlindAiConnection(*args, **kwargs)
