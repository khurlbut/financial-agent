from pathlib import Path

print("FILE:", __file__)
print("RESOLVED:", Path(__file__).resolve())
print("PARENTS[0]:", Path(__file__).resolve().parents[0])
print("PARENTS[1]:", Path(__file__).resolve().parents[1])
print("PARENTS[2]:", Path(__file__).resolve().parents[2])
