import numpy as np
from .fixed import quantize

class SchmidlCoxDetector:
    """
    Stage 3 — Schmidl-Cox Preamble Detector.
    
    Sliding-window autocorrelation across adjacent dechirped symbols.
    """
    def __init__(self, M: int, threshold: float = 0.9):
        self.M = M
        self.threshold = threshold
        
    def detect(self, rx_signal: np.ndarray):
        """
        rx_signal: (NR, L) complex array
        Returns:
            lock: bool
            timing_ref: int (index of first symbol start)
            eps_sub: float (fractional CFO bin offset)
        """
        NR, L = rx_signal.shape
        M = self.M
        
        # Dechirp the entire signal
        dechirped = np.zeros_like(rx_signal)
        for j in range(NR):
            n = np.arange(L)
            dechirped[j] = rx_signal[j] * np.exp(-1j * np.pi * (n % M)**2 / M)
            
        sc_metric = np.zeros(L - 2*M)
        for d in range(L - 2*M):
            num = 0j
            den = 0
            for j in range(NR):
                seg1 = dechirped[j, d:d+M]
                seg2 = dechirped[j, d+M:d+2*M]
                num += np.sum(seg1 * np.conj(seg2))
                den += (np.sum(np.abs(seg1)**2) + np.sum(np.abs(seg2)**2)) / 2
            
            if den > 0:
                sc_metric[d] = np.abs(num) / den
            else:
                sc_metric[d] = 0
                
        # Find peak
        peak_idx = np.argmax(sc_metric)
        if sc_metric[peak_idx] >= self.threshold:
            num = 0j
            for j in range(NR):
                seg1 = dechirped[j, peak_idx:peak_idx+M]
                seg2 = dechirped[j, peak_idx+M:peak_idx+2*M]
                num += np.sum(seg1 * np.conj(seg2))
            
            # eps_sub = ∠SC / -2π
            # Phase shift over M samples is -2π k_cfo.
            # So angle(num) = -2π k_cfo mod 2π.
            # k_cfo mod 1 = -angle(num) / 2π
            eps_sub = (-np.angle(num) / (2 * np.pi)) % 1
            return True, peak_idx, eps_sub
        
        return False, 0, 0.0

def resolve_sync(rx_preamble: np.ndarray, rx_sfd: np.ndarray, M: int, eps_sub: float):
    """
    Estimate total CFO and exact timing offset using preamble upchirps and SFD downchirps.
    
    rx_preamble: (NR, M) array, aligned to coarse timing_ref
    rx_sfd: (NR, M) array, aligned to timing_ref + SFD_offset
    """
    NR = rx_preamble.shape[0]
    ref_up = np.exp(-1j * np.pi * np.arange(M)**2 / M)
    ref_down = np.exp(1j * np.pi * np.arange(M)**2 / M)
    
    # 1. Find k_up and k_down (integer peaks)
    mag_up = np.zeros(M)
    for j in range(NR):
        mag_up += np.abs(np.fft.fft(rx_preamble[j] * ref_up))
    k_up = np.argmax(mag_up)
    
    mag_down = np.zeros(M)
    for j in range(NR):
        mag_down += np.abs(np.fft.fft(rx_sfd[j] * ref_down))
    k_down = np.argmax(mag_down)
    
    # 2. Estimate integer CFO
    # 2 * k_cfo = k_up + k_down (mod M)
    # 2 * (k_int + eps_sub) = k_up + k_down (mod M)
    # 2 * k_int = (k_up + k_down - 2 * eps_sub) (mod M)
    
    k_sum = (k_up + k_down) % M
    # Possible values for 2*k_int
    two_k_int_cand = (k_sum - 2 * eps_sub)
    
    # We want k_int to be an integer.
    # Since k_sum and 2*eps_sub might not perfectly align to an even integer,
    # we test candidates for k_int.
    k_int_cand1 = np.round(two_k_int_cand / 2.0) % M
    k_int_cand2 = (k_int_cand1 + M/2.0) % M # Ambiguity of M/2
    
    # We pick the one that best matches the k_up/k_down observations.
    # Actually, we can just use k_cfo_cand = (k_sum / 2.0) and resolve ambiguity with eps_sub.
    # Let's use a simpler way:
    k_cfo_coarse = (k_sum / 2.0) % (M / 2.0)
    # Two candidates for total k_cfo:
    cand1 = k_cfo_coarse
    cand2 = k_cfo_coarse + M/2.0
    
    # Match fractional part with eps_sub
    if np.abs((cand1 % 1) - (eps_sub % 1)) < 0.25:
        k_cfo = np.floor(cand1) + eps_sub
    else:
        k_cfo = np.floor(cand2) + eps_sub
        
    # 3. Calculate timing offset (relative to coarse ref)
    # n_off_rel = (k_up - k_cfo) mod M
    n_off_rel = (k_up - k_cfo) % M
    if n_off_rel > M/2:
        n_off_rel -= M
        
    return k_cfo, n_off_rel
