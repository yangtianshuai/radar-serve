window.RADAR_DEMO = {
 "case": {
  "filename": "AC423ccbe.nii.gz",
  "source": "NIfTI",
  "csv": "results/RADAR_infer_results_demo.csv",
  "note": "官方 demo 病例的真实推理结果（非模拟数据）"
 },
 "reference_auc_note": "外部 MERLIN 测试集上的 ROC-AUC（来源 docs/INFERENCE.md）。该指标衡量模型在该病种上的整体判别能力，不代表单个病例的判断准确率。",
 "disclaimer": "本系统输出的是 AI 预测的病灶阳性概率，仅用于科研与辅助阅片，不构成任何临床诊断意见，不能替代执业医师的判断。",
 "risk_note": "风险分级阈值（0.5 / 0.25）并非论文官方阈值，上线前应在自有验证集上用 Youden 指数重新标定。",
 "findings": [
  {
   "item": "主动脉_主动脉夹层",
   "organ": "主动脉",
   "finding": "主动脉夹层",
   "english": "Aorta_Aortic dissection",
   "probability": 0.376412,
   "risk": "medium",
   "reference_auc": null
  },
  {
   "item": "主动脉_主动脉瘤",
   "organ": "主动脉",
   "finding": "主动脉瘤",
   "english": "Aorta_Aortic aneurysm",
   "probability": 0.205838,
   "risk": "low",
   "reference_auc": 0.9903
  },
  {
   "item": "主动脉_粥样硬化",
   "organ": "主动脉",
   "finding": "粥样硬化",
   "english": "Aorta_Atherosclerosis",
   "probability": 0.57838,
   "risk": "high",
   "reference_auc": 0.8739
  },
  {
   "item": "主动脉_钙化",
   "organ": "主动脉",
   "finding": "钙化",
   "english": "Aorta_Calcification",
   "probability": 0.697321,
   "risk": "high",
   "reference_auc": null
  },
  {
   "item": "十二指肠_占位",
   "organ": "十二指肠",
   "finding": "占位",
   "english": "Duodenum_Mass",
   "probability": 0.022074,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "十二指肠_囊袋状突出影",
   "organ": "十二指肠",
   "finding": "囊袋状突出影",
   "english": "Duodenum_Saccular outpouching",
   "probability": 0.074089,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "十二指肠_憩室",
   "organ": "十二指肠",
   "finding": "憩室",
   "english": "Duodenum_Diverticulum",
   "probability": 0.071777,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "十二指肠_梗阻",
   "organ": "十二指肠",
   "finding": "梗阻",
   "english": "Duodenum_Obstruction",
   "probability": 0.012812,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "十二指肠_溃疡",
   "organ": "十二指肠",
   "finding": "溃疡",
   "english": "Duodenum_Ulcer",
   "probability": 0.075682,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_克罗恩病",
   "organ": "大肠",
   "finding": "克罗恩病",
   "english": "Large bowel_Crohn's disease",
   "probability": 0.006311,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_大肠（壁）钙化",
   "organ": "大肠",
   "finding": "大肠（壁）钙化",
   "english": "Large bowel_Mural calcification",
   "probability": 0.152692,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_急慢性（结）肠炎",
   "organ": "大肠",
   "finding": "急慢性（结）肠炎",
   "english": "Large bowel_Colitis",
   "probability": 0.029517,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_浆膜面毛糙",
   "organ": "大肠",
   "finding": "浆膜面毛糙",
   "english": "Large bowel_Serosal surface irregularity",
   "probability": 0.001923,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_溃疡性结肠炎",
   "organ": "大肠",
   "finding": "溃疡性结肠炎",
   "english": "Large bowel_Ulcerative colitis",
   "probability": 0.014654,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_直肠癌",
   "organ": "大肠",
   "finding": "直肠癌",
   "english": "Large bowel_Rectal cancer",
   "probability": 0.001607,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_积液积气",
   "organ": "大肠",
   "finding": "积液积气",
   "english": "Large bowel_Gas and fluid accumulation",
   "probability": 0.016967,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_结肠癌",
   "organ": "大肠",
   "finding": "结肠癌",
   "english": "Large bowel_Colon cancer",
   "probability": 0.001882,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_肠壁毛糙",
   "organ": "大肠",
   "finding": "肠壁毛糙",
   "english": "Large bowel_Wall irregularity",
   "probability": 0.002079,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_肠壁水肿",
   "organ": "大肠",
   "finding": "肠壁水肿",
   "english": "Large bowel_Wall edema",
   "probability": 0.068946,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_肠套叠",
   "organ": "大肠",
   "finding": "肠套叠",
   "english": "Large bowel_Intussusception",
   "probability": 0.005368,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_肠憩室",
   "organ": "大肠",
   "finding": "肠憩室",
   "english": "Large bowel_Diverticulum",
   "probability": 0.137971,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_肠梗阻",
   "organ": "大肠",
   "finding": "肠梗阻",
   "english": "Large bowel_Obstruction",
   "probability": 0.001853,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_肠穿孔",
   "organ": "大肠",
   "finding": "肠穿孔",
   "english": "Large bowel_Perforation",
   "probability": 0.009054,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_肠道扩张",
   "organ": "大肠",
   "finding": "肠道扩张",
   "english": "Large bowel_Dilatation",
   "probability": 0.010905,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_脂肪间隙模糊",
   "organ": "大肠",
   "finding": "脂肪间隙模糊",
   "english": "Large bowel_Blurring of fat planes",
   "probability": 0.005162,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "大肠_阑尾炎",
   "organ": "大肠",
   "finding": "阑尾炎",
   "english": "Large bowel_Appendicitis",
   "probability": 0.022006,
   "risk": "low",
   "reference_auc": 0.7621
  },
  {
   "item": "大肠_阑尾粪石",
   "organ": "大肠",
   "finding": "阑尾粪石",
   "english": "Large bowel_Appendicolith",
   "probability": 0.070293,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "小肠_克罗恩病",
   "organ": "小肠",
   "finding": "克罗恩病",
   "english": "Small bowel_Crohn's disease",
   "probability": 0.021085,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "小肠_套叠",
   "organ": "小肠",
   "finding": "套叠",
   "english": "Small bowel_Intussusception",
   "probability": 0.025489,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "小肠_扭转",
   "organ": "小肠",
   "finding": "扭转",
   "english": "Small bowel_Volvulus",
   "probability": 0.01831,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "小肠_梗阻",
   "organ": "小肠",
   "finding": "梗阻",
   "english": "Small bowel_Obstruction",
   "probability": 0.007078,
   "risk": "low",
   "reference_auc": 0.9704
  },
  {
   "item": "小肠_淋巴瘤",
   "organ": "小肠",
   "finding": "淋巴瘤",
   "english": "Small bowel_Lymphoma",
   "probability": 0.027062,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "小肠_积气积液",
   "organ": "小肠",
   "finding": "积气积液",
   "english": "Small bowel_Gas and fluid accumulation",
   "probability": 0.041802,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "小肠_系膜指膜炎",
   "organ": "小肠",
   "finding": "系膜指膜炎",
   "english": "Small bowel_Mesenteric panniculitis",
   "probability": 0.318798,
   "risk": "medium",
   "reference_auc": null
  },
  {
   "item": "小肠_系膜淋巴结肿大",
   "organ": "小肠",
   "finding": "系膜淋巴结肿大",
   "english": "Small bowel_Mesenteric lymphadenopathy",
   "probability": 0.069736,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "小肠_肠壁增厚",
   "organ": "小肠",
   "finding": "肠壁增厚",
   "english": "Small bowel_Wall thickening",
   "probability": 0.049689,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "小肠_肠管扩张",
   "organ": "小肠",
   "finding": "肠管扩张",
   "english": "Small bowel_Dilatation",
   "probability": 0.025514,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "小肠_脂肪瘤",
   "organ": "小肠",
   "finding": "脂肪瘤",
   "english": "Small bowel_Lipoma",
   "probability": 0.147874,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "小肠_间质瘤（胃肠间质瘤-gist）",
   "organ": "小肠",
   "finding": "间质瘤（胃肠间质瘤-gist）",
   "english": "Small bowel_Gastrointestinal stromal tumor",
   "probability": 0.022007,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "小肠_（急慢性）小肠炎",
   "organ": "小肠",
   "finding": "（急慢性）小肠炎",
   "english": "Small bowel_Enteritis",
   "probability": 0.052668,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "心脏_心包积液",
   "organ": "心脏",
   "finding": "心包积液",
   "english": "Heart_Pericardial effusion",
   "probability": 0.361593,
   "risk": "medium",
   "reference_auc": null
  },
  {
   "item": "心脏_心影（脏）增大",
   "organ": "心脏",
   "finding": "心影（脏）增大",
   "english": "Heart_Cardiomegaly",
   "probability": 0.833404,
   "risk": "high",
   "reference_auc": 0.8724
  },
  {
   "item": "肋骨_转移瘤（乳腺癌 骨转移）",
   "organ": "肋骨",
   "finding": "转移瘤（乳腺癌 骨转移）",
   "english": "Rib_Metastasis",
   "probability": 0.036811,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肋骨_骨折",
   "organ": "肋骨",
   "finding": "骨折",
   "english": "Rib_Fracture",
   "probability": 0.389049,
   "risk": "medium",
   "reference_auc": null
  },
  {
   "item": "肋骨_骨质破坏",
   "organ": "肋骨",
   "finding": "骨质破坏",
   "english": "Rib_Bone destruction",
   "probability": 0.051103,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肝_低密度影",
   "organ": "肝",
   "finding": "低密度影",
   "english": "Liver_Hypoattenuating lesion",
   "probability": 0.001625,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肝_格林森鞘积液",
   "organ": "肝",
   "finding": "格林森鞘积液",
   "english": "Liver_Periportal edema",
   "probability": 0.000727,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肝_比例失调",
   "organ": "肝",
   "finding": "比例失调",
   "english": "Liver_Lobar volume disproportion",
   "probability": 0.000296,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肝_波浪状改变",
   "organ": "肝",
   "finding": "波浪状改变",
   "english": "Liver_Undulating contour",
   "probability": 5.5e-05,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肝_硬化",
   "organ": "肝",
   "finding": "硬化",
   "english": "Liver_Cirrhosis",
   "probability": 8.6e-05,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肝_结节状强化",
   "organ": "肝",
   "finding": "结节状强化",
   "english": "Liver_Nodular enhancement",
   "probability": 0.0007,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肝_肝内胆管扩张",
   "organ": "肝",
   "finding": "肝内胆管扩张",
   "english": "Liver_Intrahepatic bile duct dilatation",
   "probability": 0.000485,
   "risk": "low",
   "reference_auc": 0.8711
  },
  {
   "item": "肝_肝内胆管结石",
   "organ": "肝",
   "finding": "肝内胆管结石",
   "english": "Liver_Hepatolithiasis",
   "probability": 7.3e-05,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肝_肝内钙化灶",
   "organ": "肝",
   "finding": "肝内钙化灶",
   "english": "Liver_Intrahepatic calcification",
   "probability": 0.002239,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肝_肝囊肿",
   "organ": "肝",
   "finding": "肝囊肿",
   "english": "Liver_Cyst",
   "probability": 0.001242,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肝_肝细胞癌",
   "organ": "肝",
   "finding": "肝细胞癌",
   "english": "Liver_Hepatocellular carcinoma",
   "probability": 8e-06,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肝_肝胆管内高密度影",
   "organ": "肝",
   "finding": "肝胆管内高密度影",
   "english": "Liver_Hyperattenuating lesion in intrahepatic bile ducts",
   "probability": 6e-05,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肝_肝血管瘤",
   "organ": "肝",
   "finding": "肝血管瘤",
   "english": "Liver_Hemangioma",
   "probability": 0.002545,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肝_胆管癌",
   "organ": "肝",
   "finding": "胆管癌",
   "english": "Liver_Intrahepatic cholangiocarcinoma",
   "probability": 1e-05,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肝_脂肪肝",
   "organ": "肝",
   "finding": "脂肪肝",
   "english": "Liver_Steatotic liver disease",
   "probability": 0.010936,
   "risk": "low",
   "reference_auc": 0.8917
  },
  {
   "item": "肝_脓肿",
   "organ": "肝",
   "finding": "脓肿",
   "english": "Liver_Abscess",
   "probability": 0.000377,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肝_转移瘤",
   "organ": "肝",
   "finding": "转移瘤",
   "english": "Liver_Metastasis",
   "probability": 0.000203,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肝_边缘不规则",
   "organ": "肝",
   "finding": "边缘不规则",
   "english": "Liver_Irregular margin",
   "probability": 7.5e-05,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肺_斑片影",
   "organ": "肺",
   "finding": "斑片影",
   "english": "Lung_Patchy opacity",
   "probability": 0.056626,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肺_气胸",
   "organ": "肺",
   "finding": "气胸",
   "english": "Lung_Pneumothorax",
   "probability": 0.048529,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肺_结节",
   "organ": "肺",
   "finding": "结节",
   "english": "Lung_Nodule",
   "probability": 0.03208,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肺_肺占位",
   "organ": "肺",
   "finding": "肺占位",
   "english": "Lung_Mass",
   "probability": 0.024529,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肺_肺萎陷",
   "organ": "肺",
   "finding": "肺萎陷",
   "english": "Lung_Pulmonary collapse",
   "probability": 0.021796,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肺_胸腔积液",
   "organ": "肺",
   "finding": "胸腔积液",
   "english": "Lung_Pleural effusion",
   "probability": 0.022446,
   "risk": "low",
   "reference_auc": 0.9574
  },
  {
   "item": "肺_膨胀不全",
   "organ": "肺",
   "finding": "膨胀不全",
   "english": "Lung_Atelectasis",
   "probability": 0.021752,
   "risk": "low",
   "reference_auc": 0.7091
  },
  {
   "item": "肺_转移瘤",
   "organ": "肺",
   "finding": "转移瘤",
   "english": "Lung_Metastasis",
   "probability": 0.010948,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肺_钙化灶",
   "organ": "肺",
   "finding": "钙化灶",
   "english": "Lung_Calcification",
   "probability": 0.030883,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肺_高密度影",
   "organ": "肺",
   "finding": "高密度影",
   "english": "Lung_Hyperattenuating opacity",
   "probability": 0.044586,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肾_低密度影",
   "organ": "肾",
   "finding": "低密度影",
   "english": "Kidney_Hypoattenuating lesion",
   "probability": 0.087856,
   "risk": "low",
   "reference_auc": 0.9122
  },
  {
   "item": "肾_囊肿",
   "organ": "肾",
   "finding": "囊肿",
   "english": "Kidney_Cyst",
   "probability": 0.097944,
   "risk": "low",
   "reference_auc": 0.9426
  },
  {
   "item": "肾_多囊肾",
   "organ": "肾",
   "finding": "多囊肾",
   "english": "Kidney_Polycystic kidney disease",
   "probability": 0.000448,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肾_实质变薄",
   "organ": "肾",
   "finding": "实质变薄",
   "english": "Kidney_Parenchymal thinning",
   "probability": 0.004692,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肾_无强化囊性灶",
   "organ": "肾",
   "finding": "无强化囊性灶",
   "english": "Kidney_Nonenhancing cystic lesion",
   "probability": 0.06735,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肾_肾动脉瘤",
   "organ": "肾",
   "finding": "肾动脉瘤",
   "english": "Kidney_Renal artery aneurysm",
   "probability": 0.129847,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肾_肾盂扩张",
   "organ": "肾",
   "finding": "肾盂扩张",
   "english": "Kidney_Renal pelvic dilatation",
   "probability": 0.270919,
   "risk": "medium",
   "reference_auc": null
  },
  {
   "item": "肾_肾盂癌",
   "organ": "肾",
   "finding": "肾盂癌",
   "english": "Kidney_Renal pelvic cancer",
   "probability": 0.034703,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肾_肾盂积水",
   "organ": "肾",
   "finding": "肾盂积水",
   "english": "Kidney_Hydronephrosis",
   "probability": 0.202137,
   "risk": "low",
   "reference_auc": 0.88
  },
  {
   "item": "肾_肾细胞癌（透明细胞癌）",
   "organ": "肾",
   "finding": "肾细胞癌（透明细胞癌）",
   "english": "Kidney_Renal cell carcinoma",
   "probability": 0.011229,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肾_肾萎缩",
   "organ": "肾",
   "finding": "肾萎缩",
   "english": "Kidney_Atrophy",
   "probability": 0.006276,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肾_肾血管平滑肌脂肪瘤",
   "organ": "肾",
   "finding": "肾血管平滑肌脂肪瘤",
   "english": "Kidney_Angiomyolipoma",
   "probability": 0.031313,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肾_肾（盂）结石",
   "organ": "肾",
   "finding": "肾（盂）结石",
   "english": "Kidney_Nephrolithiasis",
   "probability": 0.011185,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肾_高密度影",
   "organ": "肾",
   "finding": "高密度影",
   "english": "Kidney_Hyperattenuating lesion",
   "probability": 0.017919,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肾上腺_增生",
   "organ": "肾上腺",
   "finding": "增生",
   "english": "Adrenal gland_Hyperplasia",
   "probability": 0.217134,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肾上腺_结节",
   "organ": "肾上腺",
   "finding": "结节",
   "english": "Adrenal gland_Nodule",
   "probability": 0.095538,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肾上腺_脂肪瘤",
   "organ": "肾上腺",
   "finding": "脂肪瘤",
   "english": "Adrenal gland_Lipoma",
   "probability": 0.407583,
   "risk": "medium",
   "reference_auc": null
  },
  {
   "item": "肾上腺_腺瘤",
   "organ": "肾上腺",
   "finding": "腺瘤",
   "english": "Adrenal gland_Adenoma",
   "probability": 0.056856,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肾上腺_转移瘤",
   "organ": "肾上腺",
   "finding": "转移瘤",
   "english": "Adrenal gland_Metastasis",
   "probability": 0.013532,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "肾上腺_钙化",
   "organ": "肾上腺",
   "finding": "钙化",
   "english": "Adrenal gland_Calcification",
   "probability": 0.032951,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胃_壁水肿",
   "organ": "胃",
   "finding": "壁水肿",
   "english": "Stomach_Wall edema",
   "probability": 0.159493,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胃_扩张",
   "organ": "胃",
   "finding": "扩张",
   "english": "Stomach_Dilatation",
   "probability": 0.058482,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胃_胃底静脉曲张",
   "organ": "胃",
   "finding": "胃底静脉曲张",
   "english": "Stomach_Gastric fundal varices",
   "probability": 0.016125,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胃_胃溃疡",
   "organ": "胃",
   "finding": "胃溃疡",
   "english": "Stomach_Ulcer",
   "probability": 0.038867,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胃_胃癌",
   "organ": "胃",
   "finding": "胃癌",
   "english": "Stomach_Gastric cancer",
   "probability": 0.011448,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胃_间质瘤（gist）",
   "organ": "胃",
   "finding": "间质瘤（gist）",
   "english": "Stomach_Gastrointestinal stromal tumor (GIST)",
   "probability": 0.028227,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胆囊_结石",
   "organ": "胆囊",
   "finding": "结石",
   "english": "Gallbladder_Cholecystolithiasis",
   "probability": 0.005065,
   "risk": "low",
   "reference_auc": 0.9193
  },
  {
   "item": "胆囊_结节状致密影",
   "organ": "胆囊",
   "finding": "结节状致密影",
   "english": "Gallbladder_Nodular stone-like hyperattenuating lesion",
   "probability": 0.004533,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胆囊_胆囊增大",
   "organ": "胆囊",
   "finding": "胆囊增大",
   "english": "Gallbladder_Distention",
   "probability": 0.00863,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胆囊_胆囊炎",
   "organ": "胆囊",
   "finding": "胆囊炎",
   "english": "Gallbladder_Cholecystitis",
   "probability": 0.004589,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胆囊_胆囊癌",
   "organ": "胆囊",
   "finding": "胆囊癌",
   "english": "Gallbladder_Gallbladder cancer",
   "probability": 0.000136,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胆囊_胆囊腺肌症",
   "organ": "胆囊",
   "finding": "胆囊腺肌症",
   "english": "Gallbladder_Adenomyomatosis",
   "probability": 0.017837,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胆囊_胆管壁增厚",
   "organ": "胆囊",
   "finding": "胆管壁增厚",
   "english": "Gallbladder_Extrahepatic bile duct wall thickening",
   "probability": 0.001328,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胆囊_胆管扩张",
   "organ": "胆囊",
   "finding": "胆管扩张",
   "english": "Gallbladder_Extrahepatic bile duct dilatation",
   "probability": 0.001864,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胆囊_胆管炎",
   "organ": "胆囊",
   "finding": "胆管炎",
   "english": "Gallbladder_Cholangitis",
   "probability": 0.001143,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胆囊_胆管癌",
   "organ": "胆囊",
   "finding": "胆管癌",
   "english": "Gallbladder_Cholangiocarcinoma",
   "probability": 0.000253,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胆囊_胆管积气",
   "organ": "胆囊",
   "finding": "胆管积气",
   "english": "Gallbladder_Pneumobilia",
   "probability": 0.000725,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胆囊_胆管结石",
   "organ": "胆囊",
   "finding": "胆管结石",
   "english": "Gallbladder_Extrahepatic bile duct stone",
   "probability": 0.000994,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胆囊_高密度影",
   "organ": "胆囊",
   "finding": "高密度影",
   "english": "Gallbladder_Hyperattenuating lesion",
   "probability": 0.006684,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胆囊_黄色肉芽肿",
   "organ": "胆囊",
   "finding": "黄色肉芽肿",
   "english": "Gallbladder_Xanthogranuloma",
   "probability": 5.6e-05,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胰腺_低密度影",
   "organ": "胰腺",
   "finding": "低密度影",
   "english": "Pancreas_Low-density lesion",
   "probability": 0.007236,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胰腺_囊肿",
   "organ": "胰腺",
   "finding": "囊肿",
   "english": "Pancreas_Cyst",
   "probability": 0.01421,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胰腺_围脂肪间隙模糊",
   "organ": "胰腺",
   "finding": "围脂肪间隙模糊",
   "english": "Pancreas_Blurring of peripancreatic fat planes",
   "probability": 0.003264,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胰腺_肿瘤或胰腺癌",
   "organ": "胰腺",
   "finding": "肿瘤或胰腺癌",
   "english": "Pancreas_Pancreatic cancer",
   "probability": 0.000845,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胰腺_胰周假性囊肿",
   "organ": "胰腺",
   "finding": "胰周假性囊肿",
   "english": "Pancreas_Peripancreatic pseudocyst",
   "probability": 0.00041,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胰腺_胰管扩张",
   "organ": "胰腺",
   "finding": "胰管扩张",
   "english": "Pancreas_Pancreatic duct dilatation",
   "probability": 0.005614,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胰腺_胰管结石",
   "organ": "胰腺",
   "finding": "胰管结石",
   "english": "Pancreas_Pancreatic duct calculus",
   "probability": 0.000429,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胰腺_胰腺炎",
   "organ": "胰腺",
   "finding": "胰腺炎",
   "english": "Pancreas_Pancreatitis",
   "probability": 0.00402,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胰腺_胰腺饱满",
   "organ": "胰腺",
   "finding": "胰腺饱满",
   "english": "Pancreas_Enlargement",
   "probability": 0.004561,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "胰腺_萎缩",
   "organ": "胰腺",
   "finding": "萎缩",
   "english": "Pancreas_Atrophy",
   "probability": 0.004477,
   "risk": "low",
   "reference_auc": 0.9356
  },
  {
   "item": "脾_低密度灶",
   "organ": "脾",
   "finding": "低密度灶",
   "english": "Spleen_Hypoattenuating lesion",
   "probability": 0.011404,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "脾_副脾",
   "organ": "脾",
   "finding": "副脾",
   "english": "Spleen_Accessory spleen",
   "probability": 0.007828,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "脾_囊肿",
   "organ": "脾",
   "finding": "囊肿",
   "english": "Spleen_Cyst",
   "probability": 0.01252,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "脾_梗死",
   "organ": "脾",
   "finding": "梗死",
   "english": "Spleen_Infarction",
   "probability": 0.002783,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "脾_片状低密度区",
   "organ": "脾",
   "finding": "片状低密度区",
   "english": "Spleen_Patchy hypoattenuating lesion",
   "probability": 0.010848,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "脾_脾大",
   "organ": "脾",
   "finding": "脾大",
   "english": "Spleen_Splenomegaly",
   "probability": 0.008494,
   "risk": "low",
   "reference_auc": 0.9682
  },
  {
   "item": "脾_脾脏淋巴瘤",
   "organ": "脾",
   "finding": "脾脏淋巴瘤",
   "english": "Spleen_Lymphoma",
   "probability": 0.001291,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "脾_钙化",
   "organ": "脾",
   "finding": "钙化",
   "english": "Spleen_Calcification",
   "probability": 0.025723,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "膀胱_憩室",
   "organ": "膀胱",
   "finding": "憩室",
   "english": "Bladder_Diverticulum",
   "probability": 0.0114,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "膀胱_结石",
   "organ": "膀胱",
   "finding": "结石",
   "english": "Bladder_Stone",
   "probability": 0.018492,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "膀胱_膀胱壁毛糙",
   "organ": "膀胱",
   "finding": "膀胱壁毛糙",
   "english": "Bladder_Wall irregularity",
   "probability": 0.013123,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "膀胱_膀胱炎",
   "organ": "膀胱",
   "finding": "膀胱炎",
   "english": "Bladder_Cystitis",
   "probability": 0.002726,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "膀胱_膀胱癌",
   "organ": "膀胱",
   "finding": "膀胱癌",
   "english": "Bladder_Bladder cancer",
   "probability": 0.003621,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "膀胱_软组织密度影",
   "organ": "膀胱",
   "finding": "软组织密度影",
   "english": "Bladder_Soft-tissue attenuation lesion",
   "probability": 0.003923,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "门静脉_增宽",
   "organ": "门静脉",
   "finding": "增宽",
   "english": "Portal vein_Dilatation",
   "probability": 0.005472,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "门静脉_栓塞",
   "organ": "门静脉",
   "finding": "栓塞",
   "english": "Portal vein_Thrombosis",
   "probability": 0.001901,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "门静脉_高压",
   "organ": "门静脉",
   "finding": "高压",
   "english": "Portal vein_Hypertension",
   "probability": 0.00223,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "食管_增粗迂曲血管影",
   "organ": "食管",
   "finding": "增粗迂曲血管影",
   "english": "Esophagus_Dilated and tortuous tubular opacities",
   "probability": 0.008537,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "食管_管壁增厚",
   "organ": "食管",
   "finding": "管壁增厚",
   "english": "Esophagus_Wall thickening",
   "probability": 0.021218,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "食管_裂孔疝",
   "organ": "食管",
   "finding": "裂孔疝",
   "english": "Esophagus_Hiatal hernia",
   "probability": 0.100123,
   "risk": "low",
   "reference_auc": 0.8601
  },
  {
   "item": "食管_静脉扩张迂曲",
   "organ": "食管",
   "finding": "静脉扩张迂曲",
   "english": "Esophagus_Dilated and tortuous veins",
   "probability": 0.007783,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "食管_静脉曲张",
   "organ": "食管",
   "finding": "静脉曲张",
   "english": "Esophagus_Varices",
   "probability": 0.006996,
   "risk": "low",
   "reference_auc": null
  },
  {
   "item": "骶骨_骨炎",
   "organ": "骶骨",
   "finding": "骨炎",
   "english": "Sacrum_Osteitis",
   "probability": 0.005917,
   "risk": "low",
   "reference_auc": null
  }
 ]
};
