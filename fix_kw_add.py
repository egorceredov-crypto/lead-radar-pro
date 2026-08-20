with open('app/bot/handlers_user.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find line with add_keyword and insert log before it
for i, line in enumerate(lines):
    if '{"action": "add_keyword"}' in line:
        lines.insert(i, '    logger.info("KW_ADD: user=%s", cb.from_user.id)\n')
        break

with open('app/bot/handlers_user.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
print('Updated')
