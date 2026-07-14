PATH = "/home/pi/gen3d/gen3d_path.py"

with open(PATH) as f:
    src = f.read()

changes = []

# 1. VERSION bump
old = 'VERSION = "1.4"'
new = 'VERSION = "1.5"'
if old in src:
    src = src.replace(old, new, 1); changes.append("VERSION 1.4 -> 1.5")
else:
    print("WARN: VERSION not found")

# 2. Replace uniform offset with sound-scaled noise
old = '        # pure sensor drive: each point moves in/out from where it was last layer\n        desired_r = prev_r + self._target_offset'
new = (
    '        # sound level scales the amplitude of per-point random walk --\n'
    '        # loud printer = big chaotic swings, quiet = near-smooth wall\n'
    '        noise = (random.random() - 0.5) * 2.0 * self._target_offset\n'
    '        desired_r = prev_r + noise'
)
if old in src:
    src = src.replace(old, new, 1); changes.append("uniform offset -> sound-scaled noise")
else:
    print("WARN: desired_r pattern not found")

print(f"Applied {len(changes)}/2 changes:")
for c in changes:
    print(" +", c)

if len(changes) == 2:
    with open(PATH, "w") as f:
        f.write(src)
    print("WRITE OK")
else:
    print("MISMATCH -- NOT written")
