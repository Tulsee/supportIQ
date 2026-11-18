# Customer assistant: customer-prompt-v2

Model `qwen3.5:4b`.

## S2: the customer policy against the staff policy

| | Customer | Staff |
| --- | --- | --- |
| Answerable questions answered | 94% (30/32; 80%-98%) | 97% (31/32; 84%-99%) |
| Correct when answered | 100% (30/30; 89%-100%) | 97% (30/31; 84%-99%) |
| Answers that are wrong or shouldn't have been given | 0% (0/30; 0%-11%) | 3% (1/31; 1%-16%) |
| Non-answerable questions answered anyway | 0% (0/26; 0%-13%) | 0% (0/26; 0%-13%) |
| Did what the label expects | 93% (54/58; 84%-97%) | 97% (56/58; 88%-99%) |

Customer: told {'answered': 30, 'unable': 23, 'clarify': 5}; wrong answers shown: none. Staff wrong answers: ['q017'].

Actions by category (customer / staff):

- factual: {'answer': 9, 'escalate': 1} / {'answer': 9, 'escalate': 1}
- paraphrase: {'answer': 8} / {'answer': 8}
- identifier: {'answer': 8} / {'answer': 8}
- multi_part: {'answer': 5, 'abstain': 1} / {'answer': 6}
- ambiguous: {'clarify': 5, 'abstain': 1} / {'clarify': 5, 'abstain': 1}
- conflicting: {'escalate': 5, 'abstain': 1} / {'escalate': 6}
- insufficient: {'abstain': 6} / {'abstain': 6}
- unanswerable: {'abstain': 8} / {'abstain': 8}

