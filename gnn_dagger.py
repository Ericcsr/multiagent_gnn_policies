import numpy as np
import os

import torch
import torch.nn.functional as F
from torch.optim import Adam
from torch.autograd import Variable

from state_with_delay import MultiAgentStateWithDelay
from replay_buffer import ReplayBuffer
from replay_buffer import Transition
from actor import Actor

#
# # TODO: how to deal with bounded/unbounded action spaces?? Should I always assume bounded actions?


class DAGGER(object):

    def __init__(self, device, args):  # , n_s, n_a, k, device, hidden_size=32, gamma=0.99, tau=0.5):
        """
        Initialize the DDPG networks.
        :param device: CUDA device for torch
        :param args: experiment arguments
        """

        n_s = args.getint('n_states')
        n_a = args.getint('n_actions')
        k = args.getint('k')
        hidden_size = args.getint('hidden_size')
        gamma = args.getfloat('gamma')
        tau = args.getfloat('tau')

        self.n_agents = args.getint('n_agents')
        self.n_states = n_s
        self.n_actions = n_a

        # Device
        self.device = device

        hidden_layers = [hidden_size, hidden_size]
        ind_agg = 0  # int(len(hidden_layers) / 2)  # aggregate halfway

        # Define Networks
        self.actor = Actor(n_s, n_a, hidden_layers, k, ind_agg).to(self.device)

        # Define Optimizers
        self.actor_optim = Adam(self.actor.parameters(), lr=args.getfloat('actor_lr'))

        # Constants
        self.gamma = gamma
        self.tau = tau

    def select_action(self, state):
        """
        Evaluate the Actor network over the given state, and with injection of noise.
        :param state: The current state.
        :param graph_shift_op: History of graph shift operators
        :param action_noise: The action noise
        :return:
        """
        self.actor.eval()  # Switch the actor network to Evaluation Mode.
        mu = self.actor(state.delay_state, state.delay_gso)  # .to(self.device)

        # mu is (B, 1, nA, N), need (N, nA)
        mu = mu.permute(0, 1, 3, 2)
        mu = mu.view((self.n_agents, self.n_actions))

        self.actor.train()  # Switch back to Train mode.
        mu = mu.data
        return mu

        # return mu.clamp(-1, 1)  # TODO clamp action to what space?

    def gradient_step(self, batch):
        """
        Take a gradient step given a batch of sampled transitions.
        :param batch: The batch of training samples.
        :return: The loss function in the network.
        """

        delay_gso_batch = Variable(torch.cat(tuple([s.delay_gso for s in batch.state]))).to(self.device)
        delay_state_batch = Variable(torch.cat(tuple([s.delay_state for s in batch.state]))).to(self.device)
        actor_batch = self.actor(delay_state_batch, delay_gso_batch)
        optimal_action_batch = Variable(torch.cat(batch.action)).to(self.device)

        # Optimize Actor
        self.actor_optim.zero_grad()
        # Loss related to sampled Actor Gradient.
        policy_loss = F.mse_loss(actor_batch, optimal_action_batch)
        policy_loss.backward()
        self.actor_optim.step()
        # End Optimize Actor

        return policy_loss.item()

    def save_model(self, env_name, suffix="", actor_path=None):
        """
        Save the Actor Model after training is completed.
        :param env_name: The environment name.
        :param suffix: The optional suffix.
        :param actor_path: The path to save the actor.
        :return: None
        """
        if not os.path.exists('models/'):
            os.makedirs('models/')

        if actor_path is None:
            actor_path = "models/ddpg_actor_{}_{}".format(env_name, suffix)
        print('Saving model to {}'.format(actor_path))
        torch.save(self.actor.state_dict(), actor_path)

    def load_model(self, actor_path):
        """
        Load Actor Model from given paths.
        :param actor_path: The actor path.
        :return: None
        """
        print('Loading model from {}'.format(actor_path))
        if actor_path is not None:
            self.actor.load_state_dict(torch.load(actor_path).to(self.device))


def train_dagger(env, args, device):
    debug = args.getboolean('debug')
    memory = ReplayBuffer(max_size=args.getint('buffer_size'))
    learner = DAGGER(device, args)


    n_a = args.getint('n_actions')
    n_agents = args.getint('n_agents')
    batch_size = args.getint('batch_size')

    rewards = []
    total_numsteps = 0
    updates = 0

    n_episodes = 800

    beta = 1
    beta_coeff = 0.993

    best_avg_reward = -1.0 * np.Inf

    return 0

    for i in range(n_episodes):

        beta = max(beta * beta_coeff, 0.5)

        state = MultiAgentStateWithDelay(device, args, env.reset(), prev_state=None)

        episode_reward = 0
        done = False
        policy_loss_sum = 0
        while not done:

            optimal_action = env.env.controller()
            if np.random.binomial(1, beta) > 0:
                action = optimal_action
            else:
                action = learner.select_action(state)
                action = action.cpu().numpy()

            next_state, reward, done, _ = env.step(action)

            next_state = MultiAgentStateWithDelay(device, args, next_state, prev_state=state)

            total_numsteps += 1
            episode_reward += reward

            # action = torch.Tensor(action)
            notdone = torch.Tensor([not done]).to(device)
            reward = torch.Tensor([reward]).to(device)

            # action is (N, nA), need (B, 1, nA, N)
            optimal_action = torch.Tensor(optimal_action).to(device)
            optimal_action = optimal_action.transpose(1, 0)
            optimal_action = optimal_action.reshape((1, 1, n_a, n_agents))

            memory.insert(Transition(state, optimal_action, notdone, next_state, reward))

            state = next_state

        if memory.curr_size > batch_size:
            for _ in range(args.getint('updates_per_step')):
                transitions = memory.sample(batch_size)
                batch = Transition(*zip(*transitions))
                policy_loss = learner.gradient_step(batch)
                policy_loss_sum += policy_loss
                updates += 1

        if i % 10 == 0:

            episode_reward = 0
            n_eps = 1
            for n in range(n_eps):
                state = MultiAgentStateWithDelay(device, args, env.reset(), prev_state=None)
                done = False
                while not done:
                    action = learner.select_action(state)
                    next_state, reward, done, _ = env.step(action.cpu().numpy())
                    next_state = MultiAgentStateWithDelay(device, args, next_state, prev_state=state)
                    episode_reward += reward
                    state = next_state
                    # env.render()
            rewards.append(episode_reward)

            if debug:
                print(
                    "Episode: {}, updates: {}, total numsteps: {}, reward: {}, average reward: {}, policy loss: {}".format(
                        i, updates,
                        total_numsteps,
                        rewards[-1],
                        np.mean(rewards[-20:]), policy_loss_sum))

            best_avg_reward = max(best_avg_reward, np.mean(rewards[-20:]))

    env.close()
    if debug:
        learner.save_model(args.get('env'))
    return best_avg_reward
