import asyncio
import subprocess
import gym
import time
import sys
import socket
from _thread import start_new_thread
import numpy as np
import numpy.typing as npt
from typing import Optional
from gym import spaces
import psutil
from gama_client.client import GamaClient
import yaml

class GamaEnv(gym.Env):
    # USER LOCAL VARIABLES
    headless_dir: str               # Root directory for gama headless
    run_headless_script_path: str   # Path to the script that runs gama headless
    gaml_file_path: str             # Path to the gaml file containing the experiment/simulation to run
    experiment_name: str            # Name of the experiment to run

    # ENVIRONMENT CONSTANTS
    max_episode_steps: int = 11

    # Gama-server control variables
    gama_server_url: str = ""
    gama_server_port: int = -1
    gama_server_handler: GamaClient = None
    gama_server_sock_id: str = ""  # represents the current socket used to communicate with gama-server
    gama_server_exp_id: str = ""  # represents the current experiment being manipulated by gama-server
    gama_server_connected: asyncio.Event = None
    gama_server_event_loop = None
    gama_server_pid: int = -1

    # Simulation execution variables
    gama_socket = None
    gama_simulation_as_file = None  # For some reason the typing doesn't work
    gama_simulation_connection = None  # Resulting from socket create connection

    def __init__(self, headless_directory: str, headless_script_path: str,
                 gaml_experiment_path: str, gaml_experiment_name: str,
                 gama_server_url: str, env_yaml_config_path: str, gama_server_port: int):

        self.headless_dir = headless_directory
        self.run_headless_script_path = headless_script_path
        self.gaml_file_path = gaml_experiment_path
        self.experiment_name = gaml_experiment_name
        self.gama_server_url = gama_server_url
        self.gama_server_port = gama_server_port

        self.action_variables = None
        self.observation_space = None
        self._config = None
        self._load_config(env_yaml_config_path)
        for key in ['observation', 'action']:
            self._make_gym_spaces(key=key)

        # setting an event loop for the parallel processes
        self.gama_server_event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.gama_server_event_loop)
        self.gama_server_connected = asyncio.Event()

        self.init_gama_server()

        print("END INIT")

    def run_gama_server(self):
        cmd = f"cd \"{self.headless_dir}\" && \"{self.run_headless_script_path}\" -socket {self.gama_server_port}"
        print("running gama headless with command: ", cmd)
        server = subprocess.Popen(cmd, shell=True)
        self.gama_server_pid = server.pid
        print("gama server pid:", self.gama_server_pid)

    def init_gama_server(self):

        # Run gama server from the filesystem
        start_new_thread(self.run_gama_server, ())

        # try to connect to gama-server
        self.gama_server_handler = GamaClient(self.gama_server_url, self.gama_server_port)
        self.gama_server_sock_id = ""
        for i in range(30):
            try:
                self.gama_server_event_loop.run_until_complete(asyncio.sleep(2))
                print("try to connect")
                self.gama_server_sock_id = self.gama_server_event_loop.run_until_complete(
                    self.gama_server_handler.connect())
                if self.gama_server_sock_id != "":
                    print("connection successful", self.gama_server_sock_id)
                    break
            except Exception:
                print("Connection failed")

        if self.gama_server_sock_id == "":
            print("unable to connect to gama server")
            sys.exit(-1)

        self.gama_server_connected.set()

    def step(self, action):
        try:
            print("STEP")
            # sending actions
            str_action = GamaEnv.action_to_string(np.array(action)) + "\n"
            print("model sending policy:(thetaeconomy,thetamanagement,fmanagement,thetaenvironment,fenvironment)", str_action)
            print(self.gama_simulation_connection)

            self.gama_simulation_as_file.write(str_action)
            self.gama_simulation_as_file.flush()
            print("model sent policy, now waiting for reward")
            # we wait for the reward
            policy_reward = self.gama_simulation_as_file.readline()
            reward = float(policy_reward)

            print("model received reward:", policy_reward, " as a float: ", reward)
            self.state, end = self.read_observations()
            print("observations received", self.state, end)
            # If it was the final step, we need to send a message back to the simulation once everything done to acknowledge that it can now close
            # If it was the final step, we need to send a message back to the simulation once everything done to acknowledge that it can now close
            if end:
                self.gama_simulation_as_file.write("END\n")
                self.gama_simulation_as_file.flush()
                self.gama_simulation_as_file.close()
                self.gama_simulation_connection.shutdown(socket.SHUT_RDWR)
                self.gama_simulation_connection.close()
                self.gama_socket.shutdown(socket.SHUT_RDWR)
                self.gama_socket.close()
        except ConnectionResetError:
            print("connection reset, end of simulation")
        except Exception:
            print("EXCEPTION pendant l'execution")
            print(sys.exc_info()[0])
            sys.exit(-1)
        print("END STEP")
        return np.array(self.state, dtype=np.float32), reward, end, {}

    # Must reset the simulation to its initial state
    # Should return the initial observations
    def reset(self, *, seed: Optional[int] = None, return_info: bool = False, options: Optional[dict] = None):
        print("RESET")
        print("self.gama_simulation_as_file", self.gama_simulation_as_file)
        print("self.gama_simulation_connection",
              self.gama_simulation_connection)
        # Check if the environment terminated
        if self.gama_simulation_connection is not None:
            print("self.gama_simulation_connection.fileno()",
                  self.gama_simulation_connection.fileno())
            if self.gama_simulation_connection.fileno() != -1:
                self.gama_simulation_connection.shutdown(socket.SHUT_RDWR)
                self.gama_simulation_connection.close()
                self.gama_socket.shutdown(socket.SHUT_RDWR)
                self.gama_socket.close()
        if self.gama_simulation_as_file is not None:
            self.gama_simulation_as_file.close()
            self.gama_simulation_as_file = None

        tic_setting_gama = time.time()

        # Starts the simulation and get initial state
        self.gama_server_event_loop.run_until_complete(self.run_gama_simulation())

        self.wait_for_gama_to_connect()

        self.state, end = self.read_observations()
        print('\t', 'setting up gama', time.time()-tic_setting_gama)
        print('after reset self.state', self.state)
        print('after reset end', end)
        print("END RESET")
        if not return_info:
            return np.array(self.state, dtype=np.float32)
        else:
            return np.array(self.state, dtype=np.float32), {}

    def clean_subprocesses(self):
        if self.gama_server_pid > 0:
            parent = psutil.Process(self.gama_server_pid)
            for child in parent.children(recursive=True):  # or parent.children() for recursive=False
                child.kill()
            parent.kill()

    def __del__(self):
        self.clean_subprocesses()

    # Init the server + run gama
    async def run_gama_simulation(self):

        # Waiting for the gama_server websocket to be initialized
        await self.gama_server_connected.wait()

        print("creating TCP server")
        sim_port = self.init_server_simulation_control()

        # initialize the experiment
        try:
            print("asking gama-server to start the experiment")
            self.gama_server_exp_id = await self.gama_server_handler.init_experiment(self.gaml_file_path, self.experiment_name, params=[{"type": "int", "name": "port", "value": sim_port}])
        except Exception as e:
            print("Unable to init the experiment: ", self.gaml_file_path, self.experiment_name, e)
            sys.exit(-1)

        if self.gama_server_exp_id == "" or self.gama_server_exp_id is None:
            print("Unable to compile or initialize the experiment")
            sys.exit(-1)

        if not await self.gama_server_handler.play(self.gama_server_exp_id):
            print("Unable to run the experiment")
            sys.exit(-1)

    # Initialize the socket to communicate with gama
    def init_server_simulation_control(self) -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        print("Socket successfully created")

        s.bind(('', 0))  # localhost + port given by the os
        port = s.getsockname()[1]
        print("Socket bound to %s" % port)

        s.listen()
        print("Socket started listening")

        self.gama_socket = s
        return port

    # Connect with the current running gama simulation
    def wait_for_gama_to_connect(self):

        # The server is waiting for clients to connect
        self.gama_simulation_connection, addr = self.gama_socket.accept()
        print("gama connected:", self.gama_simulation_connection, addr)
        self.gama_simulation_as_file = self.gama_simulation_connection.makefile(mode='rw')

    def read_observations(self):

        received_observations: str = self.gama_simulation_as_file.readline()
        print("model received:", received_observations)

        over = "END" in received_observations
        obs = GamaEnv.string_to_nparray(received_observations.replace("END", ""))
        # obs[2]  = float(self.n_times_4_action - self.i_experience)  # We change the last observation to be the number of times that remain for changing the policy

        return obs, over

    # Converts a string to a numpy array of floats
    @classmethod
    def string_to_nparray(cls, array_as_string: str) -> npt.NDArray[np.float64]:
        # first we remove brackets and parentheses
        clean = "".join([c if c not in "()[]{}" else '' for c in str(array_as_string)])
        # then we split into numbers
        nbs = [float(nb) for nb in filter(lambda s: s.strip() != "", clean.split(','))]
        return np.array(nbs)

    # Converts an action to a string to be sent to the simulation
    @classmethod
    def action_to_string(cls, actions: npt.NDArray[np.float64]) -> str:
        return ",".join([str(action) for action in actions]) + "\n"


    def _load_config(self, env_yaml_config_path: str):
        with open(env_yaml_config_path, 'r') as file:
            self._config = yaml.safe_load(file)
        self.observation_variables = self._config['observation']
        self.action_variables = self._config['action']
        # self.context_variables = setting_dict[setting]['context']
        # if self.experiment_number is None:
          #  self.experiment_number = setting_dict[setting]['experiment_number']

    def _make_gym_spaces(self, key):
        key_spaces = {}
        if key == 'action':
            key_variables = self.action_variables
        elif key == 'observation':
            key_variables = self.observation_variables
        else:
            raise ValueError('"key" parameter must be in ["observation", "action"]')

        if not key_variables:
            return {}

        key_data = self._config[key]

        for key_variable in key_variables:
            key_variable_dic = key_data[key_variable]
            if 'type' not in [*key_variable_dic]:
                raise ValueError(f'"type" must be specified for {key} variable "{key_variable}"')
            type_ = key_variable_dic['type']
            if type_ == 'float' or type_ == 'int':
                if ('high' not in [*key_variable_dic]) or ('low' not in [*key_variable_dic]):
                    raise ValueError(f'"high" and "low" must be specified for {key} variable "{key_variable}"')
                low = key_variable_dic['low']
                high = key_variable_dic['high']
            if type_ == 'float':
                space = spaces.Box(low=low, high=high, shape=())
            elif type_ == 'discrete':
                if 'size' not in [*key_variable_dic]:
                    raise ValueError(f'"size" must be specified for {key} variable "{key_variable}"')
                size = key_variable_dic['size']
                space = spaces.Discrete(size)
            elif type_ == 'int':
                size = high - low + 1
                space = spaces.Discrete(size)

            if type_ == 'array':
                if 'subtype' not in [*key_variable_dic]:
                    raise ValueError(f'"subtype" must be specified for {key} variable "{key_variable}"')
                subtype = key_variable_dic['subtype']
                if 'size' not in [*key_variable_dic]:
                    raise ValueError(f'"size" must be specified for {key} variable "{key_variable}"')
                size = key_variable_dic['size']
                if subtype == 'float':
                    if ('high' not in [*key_variable_dic]) or ('low' not in [*key_variable_dic]):
                        raise ValueError(f'"high" and "low" must be specified for {key} variable "{key_variable}"')
                    low = key_variable_dic['low']
                    high = key_variable_dic['high']
                    space = spaces.Box(low=low, high=high, shape=(size,))
                elif subtype == 'discrete':
                    atomic_spaces = []
                    if 'subsize' not in [*key_variable_dic]:
                        raise ValueError(f'"subsize" must be specified for {key} variable "{key_variable}"')
                    sub_size = key_variable_dic['subsize']
                    for element in range(size):
                        atomic_spaces.append(spaces.Discrete(sub_size))
                    space = spaces.Tuple(atomic_spaces)
                elif subtype == 'int':
                    if ('high' not in [*key_variable_dic]) or ('low' not in [*key_variable_dic]):
                        raise ValueError(f'"high" and "low" must be specified for {key} variable "{key_variable}"')
                    low = key_variable_dic['low']
                    high = key_variable_dic['high']
                    atomic_spaces = []
                    sub_size = high - low + 1
                    for element in range(size):
                        atomic_spaces.append(spaces.Discrete(sub_size))
                    space = spaces.Tuple(atomic_spaces)
                else:
                    raise ValueError(f'{key} variable {key_variable} subtype {subtype} not in {["float", "int", "discrete"]}')

            key_spaces[key_variable] = space
            if key == 'observation':
                self.observation_space = spaces.Dict(key_spaces)
            else:
                self.action_space = spaces.Dict(key_spaces)
