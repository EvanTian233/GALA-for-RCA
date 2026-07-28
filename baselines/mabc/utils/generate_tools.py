import inspect
import re

def extract_functions(file_content):
    # Regex pattern to match function definitions and their docstrings
    pattern = re.compile(
        r"def\s+(\w+)\s*\(([^)]*)\)\s*->\s*([^:]+):\s*('''(.*?)'''|\"\"\"(.*?)\"\"\")",
        re.DOTALL  # DOTALL makes . match all characters including newlines
    )
    matches = pattern.findall(file_content)
    functions = []
    for match in matches:
        function_name = match[0]
        parameters = match[1]
        return_type = match[2].strip()
        doc = match[4] or match[5]  # Select correct docstring based on capture group
        doc = doc.strip()  # Remove whitespace around docstring
        functions.append((function_name, parameters, return_type, doc))
    return functions

def get_function_info(func_info):
    function_name, parameters_str, return_type, doc = func_info  # Unpack tuple
    # Process parameter string, convert to expected format
    # Assumes parameters_str is comma-separated parameter list
    parameters = parameters_str.split(',') if parameters_str else []
    formatted_parameters = []
    for param in parameters:
        param_name, _, param_type = param.partition(':')
        param_name = param_name.strip()
        param_type = param_type.strip() or 'Any'  # Use 'Any' if no type specified
        formatted_parameters.append(f"{param_name}: {param_type}")
    # Generate and return function definition string
    template = '''
    def {function_name}({parameters}) -> {return_type}:
    """
    {doc}
    """
    '''
    return template.format(
        function_name=function_name,
        parameters=', '.join(formatted_parameters),
        return_type=return_type or 'None',  # Use 'None' if no return type specified
        doc=doc
    ), function_name

# def extract_functions(file_content):
#     # Use regex to match function definitions
#     pattern = re.compile(r"def\s+(\w+)\s*\(([^)]*)\)\s*->\s*([^:]+):")
#     matches = pattern.findall(file_content)
#     functions = []
#     for match in matches:
#         function_name = match[0]
#         functions.append(globals()[function_name])
#     return functions

# def get_function_info(func):
#     # Function to get function info
#     signature = inspect.signature(func)
#     parameters = signature.parameters
#     template = '''
#     def {function_name}({parameters}):
#     """
#     {doc}
#     """
#     '''
#     function_name = func.__name__
#     doc = func.__doc__
#     parameters = []
#     for param_name in signature.parameters:
#         param = signature.parameters[param_name]

#         param_type = param.annotation
#         start_index = str(param_type).find("'") + 1
#         end_index = str(param_type).rfind("'")
#         param_type = str(param_type)[start_index:end_index]

#         if param.default is inspect._empty:
#             param_default = "None"
#         else:
#             param_default = param.default
#         parameters.append(f"{param_name} : {param_type} = {param_default}")
#     parameters = ", ".join(parameters)
#     return template.format(function_name=function_name, doc=doc, parameters=parameters)

def get_agent_tool_list_prompt(file_path):
    """
    Auto-generate tool_list_prompt from tool.py file
    """
    with open(file_path, "r") as file:
        file_content = file.read()
    tool_list = extract_functions(file_content)
    tool_and_tool_name_pair_list = [get_function_info(func) for func in tool_list]
    tools = [i[0] for i in tool_and_tool_name_pair_list]
    tool_names = [i[1] for i in tool_and_tool_name_pair_list]
    return "".join(tools), ", ".join(tool_names)
