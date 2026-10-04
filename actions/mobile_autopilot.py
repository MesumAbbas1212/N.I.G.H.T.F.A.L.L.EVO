import json
import time
import re
from pathlib import Path
from actions.nightfall_connect import connect_execute

def _build_prompt(instruction: str, ui_tree: dict) -> str:
    nodes = ui_tree.get("nodes", [])
    simplified_ui = []
    for i, node in enumerate(nodes):
        bounds = node.get("bounds", [0,0,0,0])
        cx = (bounds[0] + bounds[2]) // 2
        cy = (bounds[1] + bounds[3]) // 2
        desc = node.get("content_description") or node.get("text") or node.get("class")
        simplified_ui.append(f"[{i}] {desc} (clickable: {node.get('is_clickable')}) -> x:{cx}, y:{cy}")
    
    ui_text = "\n".join(simplified_ui)
    
    prompt = f"""You are a mobile UI automation agent.
Your goal is: {instruction}

Current UI elements on screen (with their center coordinates):
{ui_text}

Decide the next action to take to accomplish the goal.
Respond ONLY with a JSON object in this format:
- To tap: {{"action": "tap", "x": 100, "y": 200, "reason": "Tapping the search bar"}}
- To swipe: {{"action": "swipe", "x1": 500, "y1": 800, "x2": 500, "y2": 200, "reason": "Scrolling down"}}
- To type text: {{"action": "type", "text": "hello", "reason": "Typing message"}}
- If the goal is completely finished: {{"action": "done", "reason": "Task complete"}}
"""
    return prompt

def mobile_autopilot(parameters: dict, response=None, player=None, session_memory=None, speak=None) -> str:
    target = parameters.get("target") or parameters.get("device_id") or "Android"
    instruction = parameters.get("instruction")
    
    if not instruction:
        return json.dumps({"success": False, "error": "Missing instruction."})

    try:
        import google.generativeai as genai
        from agent.planner import _get_api_key
        genai.configure(api_key=_get_api_key())
        model = genai.GenerativeModel("gemini-3.1-flash-lite")
    except Exception as e:
        return json.dumps({"success": False, "error": f"Failed to initialize Gemini: {e}"})

    if speak:
        speak(f"Taking over {target} to complete your task.")
    elif player and hasattr(player, 'speak_async'):
        player.speak_async(f"Taking over {target} to complete your task.")
    elif player and hasattr(player, 'write_log'):
        player.write_log(f"Autopilot started on {target} for '{instruction}'")
        
    max_steps = 10
    
    for step in range(max_steps):
        dump_res_str = connect_execute({
            "target": target,
            "action": "ui_dump",
            "parameters": {}
        })
        try:
            dump_res = json.loads(dump_res_str)
        except:
            return json.dumps({"success": False, "error": "Failed to parse UI dump."})
            
        if not dump_res.get("success"):
            return dump_res_str
            
        ui_tree = dump_res.get("data", {})
        prompt = _build_prompt(instruction, ui_tree)
        
        try:
            llm_response = model.generate_content(prompt)
            text = llm_response.text.strip()
            text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
            decision = json.loads(text)
        except Exception as e:
            if player:
                player.write_log(f"Autopilot LLM error: {e}")
            break
            
        action = decision.get("action")
        reason = decision.get("reason", "Executing next step")
        
        if player and hasattr(player, 'write_log'):
            player.write_log(f"Step {step+1}: {reason}")
        if speak:
            speak(reason)
        elif player and hasattr(player, 'speak_async'):
            player.speak_async(reason)
            
        if action == "done":
            if speak:
                speak("Task completed successfully.")
            elif player and hasattr(player, 'speak_async'):
                player.speak_async("Task completed successfully.")
            return json.dumps({"success": True, "message": f"Finished: {reason}"})
            
        elif action == "tap":
            connect_execute({
                "target": target,
                "action": "ui_tap",
                "parameters": {"x": decision.get("x"), "y": decision.get("y")}
            })
            time.sleep(2)
            
        elif action == "swipe":
            connect_execute({
                "target": target,
                "action": "ui_swipe",
                "parameters": {
                    "x1": decision.get("x1"), "y1": decision.get("y1"),
                    "x2": decision.get("x2"), "y2": decision.get("y2")
                }
            })
            time.sleep(2)
            
        elif action == "type":
            connect_execute({
                "target": target,
                "action": "ui_type",
                "parameters": {"text": decision.get("text")}
            })
            time.sleep(1)
            
    return json.dumps({"success": True, "message": "Max steps reached or stopped."})
