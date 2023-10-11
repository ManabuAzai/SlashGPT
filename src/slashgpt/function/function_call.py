import json
from typing import Optional, Union

from slashgpt.chat_context import ChatContext
from slashgpt.function.function_action import FunctionAction
from slashgpt.function.jupyter_runtime import PythonRuntime
from slashgpt.manifest import Manifest
from slashgpt.utils.print import print_error, print_warning


class FunctionCall:
    """This instance represents a function call generated by LLM."""

    def __init__(self, function_call_data: dict, manifest: Manifest):
        """Use the factory classmethod to create an instance"""
        self.__function_call_data: dict = function_call_data
        self.__manifest: Manifest = manifest
        actions: dict = self.__manifest.actions()
        self.function_action: Optional[FunctionAction] = FunctionAction.factory(actions.get(self.__name()))
        """the instance that describes the action to take"""

    @classmethod
    def factory(cls, function_call_data: dict, manifest: Manifest):
        """The factory method which creates a FunctionCall instance if the function_call_data exists."""
        if function_call_data is None:
            return None
        return FunctionCall(function_call_data, manifest)

    def __str__(self):
        return f"{self.__name()}: ({self.__arguments(False)})"

    def __get(self, key: str):
        return self.__function_call_data.get(key)

    def data(self):
        """returns a dictionary with name and arguments"""
        return self.__function_call_data

    def __name(self):
        return self.__get("name")

    def get_emit_data(self, verbose: bool = False):
        """Get data to emit if it exists"""
        if self.function_action and self.function_action.has_emit():
            return (
                self.function_action.emit_data(self.__arguments(verbose)),
                self.function_action.emit_method(),
            )
        return (None, None)

    def __arguments(self, verbose: bool):
        function_name = self.__get("name")
        arguments = self.__get("arguments")
        if arguments and isinstance(arguments, str):
            try:
                return json.loads(arguments)
            except Exception:
                if verbose:
                    print_warning(f"Function {function_name}: Failed to load arguments as json")
        return arguments

    def __function_arguments(self, last_messages: dict, verbose: bool):
        arguments = self.__arguments(verbose)

        # NOTE: This is a special hack to deal with a case where GPT3/4 specify python as the function name
        # even though we never ask for it.
        if self.__manifest.get("notebook") and self.__name() == "python" and isinstance(arguments, str):
            print_warning("python function was called")
            return {"code": arguments, "query": last_messages["content"]}

        return arguments

    def get_function(self, runtime: PythonRuntime, function_name: str):
        """Returns the spacified python function"""
        if self.__manifest.get("notebook") and runtime is not None:
            return getattr(runtime, function_name)
        elif self.__manifest.get("module"):
            return self.__manifest.get_module(function_name)  # python code

    def process_function_call(self, context: ChatContext, runtime: PythonRuntime = None, verbose: bool = False):
        """Process (=execute) the function call as specified in the "actions" section or "module" section of the manifest file"""
        function_name = self.__name()
        if function_name is None:
            return (None, None, False)

        arguments = self.__function_arguments(context.last_message(), verbose)

        # Check if the action is specified in the manifest
        if self.function_action:
            # Yes, process it accordingly.
            function_message = self.function_action.call_api(arguments, self.__manifest.base_dir, verbose)
        else:
            # No. Get the specified python function and execute it.
            function = self.get_function(runtime, function_name)
            if function:
                # NOTE: This is a pure debug purpose code
                if arguments.get("code"):
                    if isinstance(arguments["code"], list):
                        print("\n".join(arguments["code"]))
                    else:
                        print(arguments["code"])
                if isinstance(arguments, str):
                    (result, message) = function(arguments)
                else:
                    (result, message) = function(**arguments)

                if message:
                    # Embed code for the context
                    context.append_message({"role": "assistant", "content": message})
                function_message = self.__format_python_result(result)
            else:
                function_message = None
                print_error(f"No execution for function {function_name}")

        if function_message:
            context.append_message({"role": "function", "content": function_message, "name": function_name})

        should_call_llm = (not self.__manifest.skip_function_result()) and function_message
        return (function_message, function_name, should_call_llm)

    def __format_python_result(self, result: Union[dict, str]):
        if isinstance(result, dict):
            result = json.dumps(result)
        result_form = self.__manifest.get("result_form")
        if result_form:
            return result_form.format(result=result)
        return result
