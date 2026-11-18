# Customer assistant: prompt-v3-ambiguity

Model `qwen3.5:4b`.

## S2: the customer policy against the staff policy

| | Customer | Staff |
| --- | --- | --- |
| Answerable questions answered | - | - |
| Correct when answered | - | - |
| Answers that are wrong or shouldn't have been given | - | - |
| Non-answerable questions answered anyway | 0% (0/12; 0%-24%) | 0% (0/12; 0%-24%) |
| Did what the label expects | 83% (10/12; 55%-95%) | 83% (10/12; 55%-95%) |

Customer: told {'clarify': 5, 'unable': 7}; wrong answers shown: none. Staff wrong answers: none.

Actions by category (customer / staff):

- ambiguous: {'clarify': 5, 'abstain': 1} / {'clarify': 5, 'abstain': 1}
- conflicting: {'escalate': 5, 'abstain': 1} / {'escalate': 5, 'clarify': 1}

