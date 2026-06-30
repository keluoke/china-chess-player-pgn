import Foundation

struct ChinesePlayerSeed {
    let fideID: String
    let chineseName: String
    let pinyinName: String
    let englishName: String
    let federation: String
    let aliases: [String]
}

enum ChinesePlayerSeeds {
    static let players: [ChinesePlayerSeed] = [
        .init(fideID: "8602883", chineseName: "王皓", pinyinName: "wang hao", englishName: "Wang, Hao", federation: "CHN", aliases: ["王皓", "wanghao", "Wang Hao", "Wang, Hao"]),
        .init(fideID: "8601429", chineseName: "王玥", pinyinName: "wang yue", englishName: "Wang, Yue", federation: "CHN", aliases: ["王玥", "wangyue", "Wang Yue", "Wang, Yue"]),
        .init(fideID: "8603006", chineseName: "丁立人", pinyinName: "ding liren", englishName: "Ding, Liren", federation: "CHN", aliases: ["丁立人", "dingliren", "Ding Liren", "Ding, Liren"]),
        .init(fideID: "8603677", chineseName: "韦奕", pinyinName: "wei yi", englishName: "Wei, Yi", federation: "CHN", aliases: ["韦奕", "weiyi", "Wei Yi", "Wei, Yi"]),
        .init(fideID: "8603405", chineseName: "余泱漪", pinyinName: "yu yangyi", englishName: "Yu, Yangyi", federation: "CHN", aliases: ["余泱漪", "yuyangyi", "Yu Yangyi", "Yu, Yangyi"]),
        .init(fideID: "8603820", chineseName: "侯逸凡", pinyinName: "hou yifan", englishName: "Hou, Yifan", federation: "CHN", aliases: ["侯逸凡", "houyifan", "Hou Yifan", "Hou, Yifan"]),
        .init(fideID: "8602980", chineseName: "居文君", pinyinName: "ju wenjun", englishName: "Ju, Wenjun", federation: "CHN", aliases: ["居文君", "juwenjun", "Ju Wenjun", "Ju, Wenjun"]),
        .init(fideID: "8603642", chineseName: "雷挺婕", pinyinName: "lei tingjie", englishName: "Lei, Tingjie", federation: "CHN", aliases: ["雷挺婕", "leitingjie", "Lei Tingjie", "Lei, Tingjie"]),
        .init(fideID: "8601283", chineseName: "赵雪", pinyinName: "zhao xue", englishName: "Zhao, Xue", federation: "CHN", aliases: ["赵雪", "zhaoxue", "Zhao Xue", "Zhao, Xue"]),
        .init(fideID: "8600538", chineseName: "叶江川", pinyinName: "ye jiangchuan", englishName: "Ye, Jiangchuan", federation: "CHN", aliases: ["叶江川", "yejiangchuan", "Ye Jiangchuan", "Ye, Jiangchuan"]),
        .init(fideID: "8600724", chineseName: "谢军", pinyinName: "xie jun", englishName: "Xie, Jun", federation: "CHN", aliases: ["谢军", "xiejun", "Xie Jun", "Xie, Jun"]),
        .init(fideID: "8600546", chineseName: "许昱华", pinyinName: "xu yuhua", englishName: "Xu, Yuhua", federation: "CHN", aliases: ["许昱华", "xuyuhua", "Xu Yuhua", "Xu, Yuhua"]),
        .init(fideID: "8600147", chineseName: "诸宸", pinyinName: "zhu chen", englishName: "Zhu, Chen", federation: "CHN", aliases: ["诸宸", "zhuchen", "Zhu Chen", "Zhu, Chen"]),
        .init(fideID: "8601950", chineseName: "卜祥志", pinyinName: "bu xiangzhi", englishName: "Bu, Xiangzhi", federation: "CHN", aliases: ["卜祥志", "buxiangzhi", "Bu Xiangzhi", "Bu, Xiangzhi"]),
        .init(fideID: "8603332", chineseName: "卢尚磊", pinyinName: "lu shanglei", englishName: "Lu, Shanglei", federation: "CHN", aliases: ["卢尚磊", "lushanglei", "Lu Shanglei", "Lu, Shanglei"]),
        .init(fideID: "8608288", chineseName: "徐翔宇", pinyinName: "xu xiangyu", englishName: "Xu, Xiangyu", federation: "CHN", aliases: ["徐翔宇", "xuxiangyu", "Xu Xiangyu", "Xu, Xiangyu"]),
        .init(fideID: "8604940", chineseName: "徐英伦", pinyinName: "xu yinglun", englishName: "Xu, Yinglun", federation: "CHN", aliases: ["徐英伦", "xuyinglun", "Xu Yinglun", "Xu, Yinglun"]),
        .init(fideID: "8602522", chineseName: "赵骏", pinyinName: "zhao jun", englishName: "Zhao, Jun", federation: "CHN", aliases: ["赵骏", "zhaojun", "Zhao Jun", "Zhao, Jun"]),
        .init(fideID: "8603847", chineseName: "曾重生", pinyinName: "zeng chongsheng", englishName: "Zeng, Chongsheng", federation: "CHN", aliases: ["曾重生", "zengchongsheng", "Zeng Chongsheng", "Zeng, Chongsheng"]),
        .init(fideID: "8603162", chineseName: "倪华", pinyinName: "ni hua", englishName: "Ni, Hua", federation: "CHN", aliases: ["倪华", "nihua", "Ni Hua", "Ni, Hua"]),
        .init(fideID: "8618020", chineseName: "鹿妙夷", pinyinName: "lu miaoyi", englishName: "Lu, Miaoyi", federation: "CHN", aliases: ["鹿妙夷", "lumiaoyi", "Lu Miaoyi", "Miaoyi Lu", "Lu, Miaoyi"]),
        .init(fideID: "8632200", chineseName: "孔祥睿", pinyinName: "kong xiangrui", englishName: "Kong, Xiangrui", federation: "CHN", aliases: ["孔祥睿", "kongxiangrui", "Kong Xiangrui", "Xiangrui Kong", "Kong, Xiangrui"]),
        .init(fideID: "8620946", chineseName: "陈一宁", pinyinName: "chen yining", englishName: "Chen, Yining", federation: "CHN", aliases: ["陈一宁", "chenyining", "Chen Yining", "Yining Chen", "Chen, Yining"]),
        .init(fideID: "8627215", chineseName: "姜天瑜", pinyinName: "jiang tianyu", englishName: "Jiang, Tianyu", federation: "CHN", aliases: ["姜天瑜", "jiangtianyu", "Jiang Tianyu", "Tianyu Jiang", "Jiang, Tianyu"])
    ]
}
