import sys
import os

# pass two files with different autotraders and run a 4 v 4 tournament, generating all the necessary json configs and 
# removing them afterwards

trader_file_1 = sys.argv[1]
trader_file_2 = sys.argv[2]

t1 = []
for i in range(4):
    t1.append("{}-TEST-{}".format(trader_file_1.split(".")[0], i+1))
    
t2 = []
for i in range(4):
    t2.append("{}-TEST-{}".format(trader_file_2.split(".")[0], i+1))

print(t1) # ['one-TEST-0', 'one-TEST-1', 'one-TEST-2', 'one-TEST-3']
print(t2) # ['two-TEST-0', 'two-TEST-1', 'two-TEST-2', 'two-TEST-3']

for trader in (t1+t2):
    os.system("cp {} {}.py".format(trader_file_2, trader))

# create .json autotrader configs

with open('autotrader_template.json') as f:
    trader_config_template = f.read()

print(trader_config_template)
print("--------")

for i, file in enumerate(t1+t2):
    name = file.split(".")[0]
    secret = "secret"
    config = trader_config_template.replace("<TEAMNAME>", name).replace("<SECRET>", secret)
    print(config)
    print()
    f = open(name + ".json", "w")
    f.write(config)
    f.close()
    # print(name, secret)

    
    

# after_replace = template_json.replace('<param placeholder>', 'param value')
# print(json.loads(after_replace)) 

print(" ".join(t1+t2))
    
# call rtg
# os.system("rm autotrader*.log; python3 rtg.py run {}".format(" ".join(t1+t2)))

# cleanup 
for trader in (t1+t2):
    os.system("rm {}.py".format(trader))
    os.system("rm {}.json".format(trader))