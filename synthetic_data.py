import numpy as np
import matplotlib.pyplot as plt
import utils
import importlib
if importlib.find_loader('oasis'):
    import oasis

def lorenz_grad(y, w):
    y1, y2, y3 = y.T
    w1, w2, w3 = w
    dy1 = w1 * (y2 - y1)
    dy2 = y1 * (w2 - y3) - y2
    dy3 = y1 *  y2 - w3  * y3
    
    return np.array([dy1, dy2, dy3]).T

def spikify_rates(r, dt):
    # r in Hz, dt in seconds
    s = np.random.poisson(r*dt)
    return s

def eulerStep(x, xgrad, dt):
    x = x + dt * xgrad
    return x

def normalize(y):
    y = y - y.mean(axis=0).mean(axis=0)
    y = y/np.abs(y).max()
    return y

def rateScale(r, maxRate):
    r = 0.5 * maxRate * (r + 1)
    return r

def RNNgrad(y, W, tau):
    ygrad = -y + W.dot(np.tanh(y))
    return ygrad / tau

def rateTransform(y, W, b):
    B, T, N = y.shape
    r = np.zeros((B, T, W.shape[1]))
    for t in range(T):
        r[:, t] = np.exp((y[:, t].dot(W) + b).clip(-20, 20))        
    return r

def split_data(data, split_ix):
    data_train = data[:split_ix]
    data_valid = data[split_ix:]
    return data_train, data_valid

def calcium_grad(c, tauc):
    cgrad = -c/tauc
    return cgrad

def deconvolve_calcium(C, g=0.5):
    num_trials, num_steps, num_cells = C.shape
    S = np.zeros_like(C)
    for trial in range(num_trials):
        for cell in range(num_cells):
            c,s = oasis.functions.oasisAR1(C[trial, :, cell], g=g, s_min=0.5)
            S[trial, :, cell] = s.round()
    return S

def generate_lorenz_data(N_trials, N_inits, N_cells, N_steps, N_stepsinbin = 1,
                         dt_lorenz=None, dt_spike=None, dt_cal=None,
                         base_firing_rate = 5.0,
                         tau_c = 0.4, inc_c = 1.0, sigma=0.2,
                         trainp= 0.8, seed=100, save=True):
    N_lorenz = 3
    assert N_steps%N_stepsinbin == 0, 'Can\'t bin time steps'
    N_steps_bin = int(N_steps/N_stepsinbin)
    if dt_lorenz is None:
        dt_lorenz = np.clip(2.0/N_steps, 0.005, 0.02)
    
    if dt_spike is None:
        dt_spike = dt_lorenz
        
    if dt_cal is None:
        dt_cal = dt_spike * N_stepsinbin
        
    N_train = int(N_trials * trainp)
    N_steps_burn = max(N_steps, 300)

    y = np.zeros((N_inits, N_steps + N_steps_burn, N_lorenz))
    
    w_lorenz = ([10.0, 28.0, 8.0/3.0]);
    y[:, 0] = np.random.randn(N_inits, N_lorenz)
    for step in range(1, N_steps + N_steps_burn):
        dy = lorenz_grad(y[:, step - 1], w_lorenz)
        y[:, step] = eulerStep(y[:, step-1], dy, dt_lorenz)
        
    y = y[:, N_steps_burn:]
    y = normalize(y)
    
    
    W = (np.random.rand(N_lorenz, N_cells) + 1) * np.sign(np.random.randn(N_lorenz, N_cells))
    b = np.log(base_firing_rate)
    
    rates  = np.exp(y.dot(W) + b)
    spikes = np.array([np.random.poisson(rates * dt_spike) for trial in range(N_trials)])
    
    if N_stepsinbin > 1:
        from scipy.stats import binned_statistic
        binned_latent = np.zeros((N_trials, N_inits, N_steps_bin, N_lorenz))
        binned_rates  = np.zeros((N_trials, N_inits, N_steps_bin, N_cells))
        binned_spikes = np.zeros((N_trials, N_inits, N_steps_bin, N_cells))
        for ix in range(N_trials):
            for jx in range(N_inits):
                binned_spikes[ix, jx] = binned_statistic(x=np.arange(N_steps), values=spikes[ix, jx].T, statistic='sum',  bins=N_steps_bin)[0].T
                binned_rates[ix, jx]  = binned_statistic(x=np.arange(N_steps), values=rates[jx].T, statistic='mean', bins=N_steps_bin)[0].T
                binned_latent[ix, jx] = binned_statistic(x=np.arange(N_steps), values=y[jx].T, statistic='mean', bins=N_steps_bin)[0].T
        rates = binned_rates; del binned_rates
        spikes = binned_spikes.astype(int); del binned_spikes
        latent = binned_latent; del binned_latent
    else:
        latent = np.array([y for trial in range(N_trials)])
        rates  = np.array([rates for trial in range(N_trials)])
    
    calcium = np.zeros_like(spikes, dtype=float)
    fluor   = np.zeros_like(spikes, dtype=float)
    
    ct = spikes[:, :, 0, :]*inc_c 
    calcium[:, :, 0, :] = ct
    fluor[:, :, 0, :]   = ct + np.random.randn(N_trials, N_inits, N_cells)*sigma
    
    for step in range(1, N_steps_bin):
        ct = eulerStep(ct, calcium_grad(ct, tau_c), dt_cal)
        ct = ct + inc_c*spikes[:, :, step, :]
        calcium[:, :, step, :] = ct
        fluor[:, :, step, :]   = ct + np.random.randn(N_trials, N_inits, N_cells)*sigma
    
    data_dict = {}
    for data, name in zip([latent, rates, spikes, calcium, fluor], ['latent', 'rates', 'spikes', 'calcium', 'fluor']):
        data_dict['train_%s'%name] = np.reshape(data[:N_train], (N_train * N_inits, N_steps_bin, data.shape[-1]))
        data_dict['valid_%s'%name] = np.reshape(data[N_train:], ((N_trials - N_train) * N_inits, N_steps_bin, data.shape[-1]))
        
    if importlib.find_loader('oasis'):
        data_dict['train_oasis'] = deconvolve_calcium(data_dict['train_fluor'], g=np.exp(-dt_cal/tau_c))
        data_dict['valid_oasis'] = deconvolve_calcium(data_dict['valid_fluor'], g=np.exp(-dt_cal/tau_c))
    
    data_dict['train_data']  = data_dict['train_spikes']
    data_dict['valid_data']  = data_dict['valid_spikes']
    data_dict['train_truth'] = data_dict['train_rates']
    data_dict['valid_truth'] = data_dict['valid_rates']
    data_dict['dt']          = dt_cal
    
    data_dict['loading_weights'] = W
    
    data_dict['conversion_factor'] = 1./(np.max(rates) * dt_cal)
    
    if save:
        utils.write_data('./synth_data/lorenz_%03d'%seed, data_dict)
        
    return data_dict


def generate_chaotic_rnn_data(T= 1, dt_rnn= 0.01, dt_cal= 0.1,
                              Ninits= 400, Ntrial= 10, Ncells= 50, trainp= 0.8,
                              tau=0.025, gamma=1.5, maxRate=5, B=20,
                              tau_c = 0.4, inc_c=1.0, sigma=0.2,
                              seed=5, save=False):
    
    '''
    Generate synthetic calcium fluorescence data from chaotic recurrent neural network system
    
    Arguments:
        - T (int or float): total time in seconds to run 
        - dt_rnn (float): time step of chaotic RNN
        - dt_cal (float): time step of calcium trace
        - Ninits (int): Number of network initialisations
        - Ntrial (int): Number of instances with same network initialisations
        - Ncells (int): Number of cells in network
        - trainp (float): proportion of dataset to partition into training set
        - tau (float): time constant of chaotic RNN
        - gamma (float): 
        - maxRate (float): maximum firing rate of chaotic RNN
        - B (int, or float): amplitude of perturbation to network
        - tau_c (float): time constant of calcium decay
        - inc_c (float): increment size of calcium influx
        - sigma (float): standard deviation of fluorescence noise
        - save (bool): save output
    '''
    
    np.random.seed(seed)

    Nsteps = int(T / dt_rnn)
    Ntrial_train = int(trainp * Ntrial)
    
    # Chaotic RNN weight matrix
    W = gamma*np.random.randn(Ncells, Ncells)/np.sqrt(Ncells)

    rates, spikes, calcium, fluor = np.zeros((4, Ninits, Ntrial, Nsteps, Ncells))
    
    perturb_steps = []

    for init in range(Ninits):
        y0 = np.random.randn(Ncells)

        for trial in range(Ntrial):
            perturb_step = np.random.randint(0.25*Nsteps,0.75*Nsteps)
            perturb_steps.append(perturb_step)
            perturb_amp = np.random.randn(Ncells)*B
            b = 0

            yt = y0
            rt = rateScale(np.tanh(yt), maxRate=maxRate)
            st = spikify_rates(rt, dt=dt_cal)
            ct = inc_c*st

            rates[init, trial, 0, :]   = rt
            spikes[init, trial, 0, :]  = st
            calcium[init, trial, 0, :] = ct
            fluor[init, trial, 0, :]   = ct + np.random.randn(Ncells)*sigma

            for step in range(1, Nsteps):
                yt = eulerStep(yt, RNNgrad(yt+b, W, tau), dt_rnn)
                ct = eulerStep(ct, calciumgrad(ct, tau_c),  dt_cal)

                if step == perturb_step:
                    b = perturb_amp*dt_rnn/tau
                else:
                    b = 0

                rt = rateScale(np.tanh(yt), maxRate=maxRate)
                st = spikify_rates(rt, dt=dt_cal)
                ct = ct + inc_c*st

                rates[init, trial, step, :]   = rt
                spikes[init, trial, step, :]  = st
                calcium[init, trial, step, :] = ct
                fluor[init, trial, step, :]   = ct + np.random.randn(Ncells)*sigma
    
    # Construct data dictionary
    
    for data, name in zip([rates, spikes, calcium, fluor], ['rates', 'spikes', 'calcium', 'fluor']):
        data_dict['train_%s'%name] = np.reshape(data[:N_train], (N_train * N_inits, N_steps_bin, data.shape[-1]))
        data_dict['valid_%s'%name] = np.reshape(data[N_train:], ((N_trials - N_train) * N_inits, N_steps_bin, data.shape[-1]))
        
    if importlib.find_loader('oasis'):
        data_dict['train_oasis'] = deconvolve_calcium(data_dict['train_fluor'], g=np.exp(-dt_cal/tau_c))
        data_dict['valid_oasis'] = deconvolve_calcium(data_dict['valid_fluor'], g=np.exp(-dt_cal/tau_c))
    
    data_dict['train_data']  = data_dict['train_spikes']
    data_dict['valid_data']  = data_dict['valid_spikes']
    data_dict['train_truth'] = data_dict['train_rates']
    data_dict['valid_truth'] = data_dict['valid_rates']
    data_dict['dt']          = dt_cal
    data_dict['perturb_times'] = np.array(perturb_steps)*dt_cal
    
    
    if save:
        utils.write_data('./synth_data/chaotic_rnn_%03d'%seed, data_dict)
        
    return data_dict