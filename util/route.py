import ast


def parse_route(content):
    tmp = ast.literal_eval(f"[{content}]")
    for i in tmp:
        i["lat"] = float(i["lat"])
        i["lng"] = float(i["lng"])
    return tmp
