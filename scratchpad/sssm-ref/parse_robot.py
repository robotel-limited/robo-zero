#!/usr/bin/env python3
"""Parse Tecnomatix.Engineering.xml for Robot types and TxTransformation methods."""

import xml.etree.ElementTree as ET

FILE = "/srv/robo-zero/workdir/projects/103c56da730a/tx/Tecnomatix.Engineering.xml"

tree = ET.parse(FILE)
root = tree.getroot()

# Collect all member names
members = []
for member in root.findall("members/member"):
    name = member.get("name", "")
    members.append(name)

print(f"Total members: {len(members)}")
print()

# Q1: Find T: types directly in namespace Tecnomatix.Engineering with "Robot" in name
# T: member format: T:Tecnomatix.Engineering.TypeName
# Direct in namespace: no dots after the first one (no sub-namespace, no nested)
# Case-sensitive "Robot" in name

robot_types = []
for name in members:
    if not name.startswith("T:Tecnomatix.Engineering."):
        continue
    path = name[len("T:Tecnomatix.Engineering."):].strip()
    # Direct type: no dots (no sub-namespace, no nested type)
    if path.count(".") > 0:
        continue
    if "Robot" in path:
        robot_types.append(path)

print("=" * 70)
print("Q1: Types directly in namespace Tecnomatix.Engineering with 'Robot' in name")
print("=" * 70)
print(f"Count: {len(robot_types)}")
for t in sorted(robot_types):
    print(f"  T:Tecnomatix.Engineering.{t}")
print()

# Collect methods for each robot type
type_methods = {}
for name in members:
    if not name.startswith("M:Tecnomatix.Engineering."):
        continue
    path = name[len("M:Tecnomatix.Engineering."):].strip()
    parts = path.split(".", 1)
    if len(parts) < 2:
        continue
    type_name = parts[0]
    if type_name in robot_types:
        if type_name not in type_methods:
            type_methods[type_name] = []
        type_methods[type_name].append(name)

# Q2 & Q3: Find non-ctor methods with TxTransformation param
# A method is non-ctor if it doesn't contain "#ctor"
# It takes TxTransformation if the param type is exactly "Tecnomatix.Engineering.TxTransformation"
# (not inside a generic like TxObjectList<Tecnomatix.Engineering.TxTransformation>)

def has_exact_txtrans_param(m_name):
    """Check if method has a param of exact type Tecnomatix.Engineering.TxTransformation."""
    if "#ctor" in m_name:
        return False
    # Extract the parameter part (inside parentheses)
    # Format: M:Tecnomatix.Engineering.Type.Method(params)
    # The params are in the parentheses
    # Find the last "(" and the matching ")"
    paren_start = m_name.rfind("(")
    if paren_start == -1:
        return False
    # Get the params string (everything between parens)
    # Need to handle nested parens in generic types
    params_str = m_name[paren_start+1:]
    # Remove the closing paren if present
    # Actually, let me just check the params string
    # A parameter of exact type TxTransformation would appear as:
    # (Tecnomatix.Engineering.TxTransformation)
    # or in a list: (System.String, Tecnomatix.Engineering.TxTransformation)
    # But NOT as: (System.Collections.Generic.List<Tecnomatix.Engineering.TxTransformation>)
    
    # The key check: does "Tecnomatix.Engineering.TxTransformation" appear 
    # in the params, and is it NOT inside angle brackets?
    # Remove all <...> blocks and check
    temp = params_str
    # Remove all <...> blocks (generic type arguments)
    import re
    # Remove everything between < and > (generic type params)
    temp = re.sub(r'<[^>]*>', '', temp)
    
    # Now check if "Tecnomatix.Engineering.TxTransformation" is in the remaining string
    # But we also need to be careful - the type name "Tecnomatix.Engineering.TxTransformation"
    # could be a parameter type directly. Let me check if it appears as a parameter.
    # After removing <...>, if "Tecnomatix.Engineering.TxTransformation" is still there,
    # it means it's a direct parameter type (not a generic argument).
    
    return "Tecnomatix.Engineering.TxTransformation" in temp

qualifying_types = []
all_methods = []

for t in sorted(robot_types):
    if t not in type_methods:
        continue
    methods = type_methods[t]
    qualifying_method_names = set()
    
    for m in methods:
        if has_exact_txtrans_param(m):
            # Extract method name
            # Format: M:Tecnomatix.Engineering.TypeName.MethodName(params)
            method_name = m.split("(")[0]  # M:Tecnomatix.Engineering.TypeName.MethodName
            method_name = method_name.split(".")[-1]  # Just the method name
            qualifying_method_names.add(method_name)
    
    if qualifying_method_names:
        qualifying_types.append(t)
        all_methods.extend(qualifying_method_names)
        print(f"  T:Tecnomatix.Engineering.{t}")
        for mn in sorted(qualifying_method_names):
            print(f"    - {mn}")
        print()

print("=" * 70)
print("Q4: Totals")
print("=" * 70)
print(f"Total qualifying types: {len(qualifying_types)}")
unique_methods = set(all_methods)
print(f"Total unique methods: {len(unique_methods)}")
print()
print("All unique method names:")
for m in sorted(unique_methods):
    print(f"  - {m}")

# Debug: show all methods for each robot type
print()
print("=" * 70)
print("DEBUG: All methods for each robot type")
print("=" * 70)
for t in sorted(robot_types):
    if t in type_methods:
        print(f"\nT:Tecnomatix.Engineering.{t} ({len(type_methods[t])} methods):")
        for m in type_methods[t]:
            is_ctor = "#ctor" in m
            has_tx = "Tecnomatix.Engineering.TxTransformation" in m
            marker = ""
            if is_ctor:
                marker += "[ctor] "
            if has_tx:
                marker += "[TxTrans] "
            print(f"  {marker}{m}")
