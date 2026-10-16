import torch
print("PyTorch:", torch.__version__)
print("CUDA 可用:", torch.cuda.is_available())
print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "无")
