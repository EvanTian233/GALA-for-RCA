from pathlib import Path
from random import uniform
from mABC.utils.llm import llm_chat
from mABC.utils.generate_tools import get_agent_tool_list_prompt
from mABC.utils.act_eval import act_eval
from mABC.agents.base.profile import AgentWorkflow

STOP_WORDS_NONE = ""
STOP_WORDS_REACT = "\nObservation"

REACT_STATUS_RE = "Reason"
REACT_STATUS_ACT = "Act"
REACT_STATUS_FINISH = "Finish"

TOT_CHILDREN_NUM = 1

class BaseRun:
    def __init__(self):
        pass

    def qa(self, messages, stop_words=STOP_WORDS_NONE):
        answer = llm_chat(messages, stop_words=stop_words)
        print("*" * 50)
        print(messages)
        print("*" * 50)
        print(f"A: {answer}")
        print("*" * 50, end="\n\n")        
        return answer

    def run(self, agent: AgentWorkflow, question: str):
        messages = [
            {"role": "system", "content": f"{agent.role_desc}{agent.base_prompt}"},
            {"role": "user", "content": question},
        ]
        answer = self.qa(messages, stop_words=STOP_WORDS_NONE)
        messages.append({"role": "assistant", "content": answer})
        return messages

class ThreeHotCotRun(BaseRun):
    def __init__(self, alpha=-1, beta=-1):
        self.alpha = alpha  # Support rate threshold
        self.beta = beta  # Participation rate threshold
        self.w_c_max = 1.5  # Maximum contribution index
        self.w_e_max = 1.5  # Maximum expertise index
        self.delta = 0.03  # Maximum decay rate for contribution index

    def run(self, agents, poll_role, poll_problem, poll_content):
        poll_initiator = ""
        poll_reason = ""
        total_weight = sum(agent.weight for agent in agents)
        for agent in agents:
            poll_result = self.poll(agent, poll_role, poll_problem, poll_content)
            if poll_result['poll'] == "Yes":
                poll_initiator = agent.role_name
                poll_reason = poll_result['reason']
                break
        if self.alpha == -1 and self.beta == -1:
            return True
        # No poll
        if poll_initiator == "":
            run_result = True
        else:
            vote_results = []
            vote_weights = {"For": 0, "Against": 0, "Abstain": 0}
            for agent in agents:
                # vote_result = self.submit_vote(agent, poll_initiator, poll_reason, poll_role, poll_problem, poll_content)
                # vote_results.append(vote_result)
                vote_result = self.submit_vote(agent, poll_initiator, poll_reason, poll_role, poll_problem, poll_content)
                vote_results.append(vote_result)
                vote_weights[vote_result] += agent.weight
            # for_num = vote_results.count("For")
            # against_num = vote_results.count("Against")
            # abstain_num = vote_results.count("Abstain")
            # run_result = for_num >= (for_num + against_num + abstain_num) / 2
            support_rate = vote_weights["For"] / total_weight
            participation_rate = (vote_weights["For"] + vote_weights["Against"]) / total_weight
            run_result = support_rate >= self.alpha and participation_rate >= self.beta
        # Update weights
        self.update_weights(agents, vote_results, run_result)
        # back
        return run_result
    
    def update_weights(self, agents, vote_results, run_result):
        """
        Update the voting weights of agents based on their participation and decision accuracy.

        Args:
            agents (list): List of AgentWorkflow instances.
            vote_results (list): List of voting results from agents.
            run_result (bool): The outcome of the voting process.
        """
        for agent, vote in zip(agents, vote_results):
            # Update contribution index w_c
            agent.contribution_index = min(agent.contribution_index * (1 - uniform(0, self.delta)), self.w_c_max)
            if vote != "Abstain":
                agent.contribution_index += 0.1  # Active participation increment
                agent.contribution_index = min(agent.contribution_index, self.w_c_max)

            # Update expertise index w_e based on the accuracy of the decision
            if ((vote == "For" and run_result) or (vote == "Against" and not run_result)):
                agent.expertise_index += 0.01  # Correct decision increment
            else:
                agent.expertise_index -= 0.01  # Incorrect decision decrement
            agent.expertise_index = max(min(agent.expertise_index, self.w_e_max), 1.0)  # Ensure within [1.0, w_e_max]

            # Update agent's overall weight
            agent.weight = agent.contribution_index * agent.expertise_index
    
    def poll(self, agent: AgentWorkflow, poll_role, poll_problem, poll_content):
        messages = [
            {"role": "system", "content": f"{agent.role_desc}{agent.base_prompt}"},
            {"role": "user", "content": f"{agent.poll_prompt}".format(poll_role=poll_role, poll_problem=poll_problem, poll_content=poll_content)},
        ]
        answer = self.qa(messages, stop_words=STOP_WORDS_NONE)
        result = self.parse_in_poll(answer)
        return result
    
    def parse_in_poll(self, answer):
        result = {
            "poll": None,
            "reason": None,
        }
        if "Poll:" in answer and "Reason:" in answer:
            result["poll"] = answer.split("Poll:")[1].split("\n")[0].strip()
            result["reason"] = answer.split("Reason:")[1].strip()
        if result["poll"] not in ["Yes", "No"]:
            if "Yes" in answer:
                result["poll"] = "Yes"
            elif "No" in answer:
                result["poll"] = "No"
        return result

    def submit_vote(self, agent: AgentWorkflow, poll_initiator, poll_reason, poll_role, poll_problem, poll_content):
        messages = [
            {"role": "system", "content": f"{agent.role_desc}{agent.base_prompt}"},
            {"role": "user", "content": f"{agent.vote_prompt}".format(poll_initiator=poll_initiator, poll_reason=poll_reason, poll_role=poll_role, poll_problem=poll_problem, poll_content=poll_content)},
        ]
        answer = self.qa(messages, stop_words=STOP_WORDS_NONE)
        result = self.parse_in_vote(answer)
        return result
    
    def parse_in_vote(self, answer):
        result = {
            "option": None
        }
        if "Option: " in answer:
            result["option"] = answer.split("Option:")[1].split("\n")[0].strip()
        if result["option"] not in ["For", "Against", "Abstain"]:
            if "For" in answer:
                result["option"] = "For"
            elif "Against" in answer:
                result["option"] = "Against"
            elif "Abstain" in answer:
                result["option"] = "Abstain"
        return result

class ReActTotRun(BaseRun):
    def __init__(self):
        pass

    def run(self, agent: AgentWorkflow, question: str, agent_tool_env, eval_run, agents, history="", index=0):
        history = f"Question: {question}" if history == "" else history
        step_status_record_list = self.sample_multi_next_step(agent, question, agent_tool_env, eval_run, agents, history)
        # index = self.eval_step_output(agent, question, history, step_status_record_list)
        index = 0
        best_step_status_record = step_status_record_list[index]
        # best_step_status_record = step_status_record_list[0]
        history = history + best_step_status_record["record"]
        if best_step_status_record["status"] != REACT_STATUS_FINISH:
            return self.run(agent, question, agent_tool_env, eval_run, agents, history, index + 1)
        else:
            return history.split("Final Answer:")[1].strip()
            return history

    def sample_multi_next_step(self, agent: AgentWorkflow, question, agent_tool_env, eval_run, agents, history="", num=TOT_CHILDREN_NUM):
        step_status_record_list = []
        for i in range(num):
            # status, step_record = self.run_one_step(agent, question, agent_tool_env, history)
            status, step_record = self.eval_and_run_one_step(agent, question, agent_tool_env, eval_run, agents, history)
            step_status_record_list.append(
                {
                    "status": status,
                    "record": step_record,
                }
            )
        return step_status_record_list
    
    def eval_and_run_one_step(self, agent: AgentWorkflow, question, agent_tool_env, eval_run: ThreeHotCotRun, agents, history=""):
        status, step_record = self.run_one_step(agent, question, agent_tool_env, history)
        # result = eval_run.run(agents, agent.role_name, question, history + step_record)
        result = True
        # If vote result is True, proceed to next step
        if result:
            return status, step_record
        # Otherwise, re-execute this step
        else:
            return self.eval_and_run_one_step(agent, question, agent_tool_env, eval_run, agents, history)

    # REACT_STATUS_RE => REACT_STATUS_ACT/REACT_STATUS_FINISH
    # REACT_STATUS_ACT => REACT_STATUS_RE
    def run_one_step(self, agent: AgentWorkflow, question, agent_tool_env, history=""):
        # history stores all past operations and thoughts
        history = f"Question: {question}" if history == "" else history
        status = REACT_STATUS_RE
        step_record = ""
        while status == REACT_STATUS_RE:
            # When in RE state, accumulate previous output and history as new input
            step_input = history
            result = self.reason(agent, step_input)
            status = result["status"]
            thought = result["thought"]
            step_record += f"\nThought: {thought}"  # Add thought to history
        if status == REACT_STATUS_ACT:
            # If in ACT state, execute the action and update status
            action_tool_name = result["action_tool_name"]
            action_tool_input = result["action_tool_input"]
            step_record += f"\nAction Tool Name: {action_tool_name}"
            step_record += f"\nAction Tool Input: {action_tool_input}"
            action = f"{action_tool_name}({action_tool_input})"
            status, step_output = self.act(action, agent_tool_env)  # Execute action
            step_record += f"\nObservation: the result of {action} is {step_output}"  # Add output to history
        elif status == REACT_STATUS_FINISH:
            final_answer = result["final_answer"]
            step_record += f"\nFinal Answer: {final_answer}"  # Record final answer to history
        return status, step_record

    def reason(self, agent: AgentWorkflow, question):
        _mabc_root = Path(__file__).resolve().parents[2]
        tools, tool_names = get_agent_tool_list_prompt(str(_mabc_root / agent.tool_path))
        messages = [
            {"role": "system", "content": f"{agent.role_desc}{agent.tool_prompt}{agent.base_prompt}".format(tools=tools, tool_names=tool_names)},
            {"role": "user", "content": question},
        ]
        answer = self.qa(messages, stop_words=STOP_WORDS_REACT)
        # messages.append({"role": "assistant", "content": answer})
        result = self.parse(answer)
        return result

    def parse(self, answer):
        # Check if answer contains thought process
        result = {
            "status": REACT_STATUS_RE,
            "thought": None,
            "final_answer": None,
            "action_tool_name": None,
            "action_tool_input": None,
        }
        if "Thought:" in answer:
            # Extract thought content
            result["thought"] = (
                answer.split("Thought:")[1]
                .split("Action")[0]
                .split("Final Answer:")[0]
                .strip()
            )
        # Check if answer contains final answer
        if "Final Answer:" in answer:
            # Extract final answer and return finish status
            result["final_answer"] = answer.split("Final Answer:")[1].strip()
            result["status"] = REACT_STATUS_FINISH
            return result
        elif "Action Tool Name:" in answer and "Action Tool Input:" in answer:
            # Extract action command and return action status
            action_tool_name = (
                answer.split("Action Tool Name:")[1]
                .split("Action Tool Input:")[0]
                .strip()
            )
            action_tool_input = (
                answer.split("Action Tool Input:")[1].split("Observation:")[0].strip()
            )
            result["action_tool_name"] = action_tool_name
            result["action_tool_input"] = action_tool_input
            result["status"] = REACT_STATUS_ACT
            return result
        # If no final answer and no action command, return to thinking state
        else:
            return result

    def act(self, action, agent_tool_env):
        action_result = act_eval(action, agent_tool_env)
        return REACT_STATUS_RE, action_result  # Return to reasoning state after action

    def eval_step_output(self, agent: AgentWorkflow, question, history, step_status_record_list):
        return 0


