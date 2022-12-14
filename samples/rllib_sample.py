import gym
import os
from ray.rllib.agents.ppo import PPOTrainer #Import from rllib the algorithm you want to use, in this case PPO
from ray.tune.registry import register_env # Import for registering the environment
from gama_gym.envs import GamaEnv #Import the environment to use
import user_local_variables as lv

CURRENT_DIRECTORY = os.path.abspath(os.getcwd())

# register the custom environment in ray
env = 'GamaEnv-v0'
register_env(env, lambda config: GamaEnv(headless_directory      = lv.headless_dir,
                          headless_script_path    = lv.run_headless_script_path,
                          gaml_experiment_path    = CURRENT_DIRECTORY+r"/TCP_model_env_rc2.gaml",
                          gaml_experiment_name    = "one_simulation",
                          gama_server_url         = "localhost",
                          gama_server_port        = 6868,
                          )             
            )
# Create our RLlib Trainer.
config = {
   "env": "GamaEnv-v0", # Environment (RLlib understands openAI gym registered strings),
   "num_workers": 1,    # Use environment workers (aka "rollout workers") that parallely collect samples from their own environment clone(s).
   "framework": "tf",   # Change this to "framework: torch", if you are using PyTorch. Also, use "framework: tf2" for tf2.x eager execution.
   "model": {
         "fcnet_hiddens": [32],
         "fcnet_activation": "relu",
         }, # Tune default model given the environment's observation- and action spaces.
   "rollout_fragment_length": 20,
   "train_batch_size": 20,
   "sgd_minibatch_size": 10,
   }
trainer = PPOTrainer(config=config)

# Run it for n training iterations. A training iteration includes
# parallel sample collection by the environment workers as well as
# loss calculation on the collected batch and a model update.
for _ in range(1):
   # Perform one iteration of training the policy with PPO
   result = trainer.train()
   print('result')
   print(result)
