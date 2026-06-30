import Foundation

struct RecommendedYouthSeed: Hashable {
    let fideID: String
    let chineseName: String
    let pinyinName: String
    let englishName: String
    let birthYear: String
    let focus: String
    let tags: [String]
}

enum RecommendedYouthSeeds {
    static let players: [RecommendedYouthSeed] = [
        .init(
            fideID: "8618020",
            chineseName: "鹿妙夷",
            pinyinName: "lu miaoyi",
            englishName: "Lu, Miaoyi",
            birthYear: "2010",
            focus: "女子世界级新星，成人赛与国际公开赛活跃",
            tags: ["IM/WGM", "中国女锦标赛", "国际公开赛"]
        ),
        .init(
            fideID: "8632200",
            chineseName: "孔祥睿",
            pinyinName: "kong xiangrui",
            englishName: "Kong, Xiangrui",
            birthYear: "2009",
            focus: "男子新锐，亚洲个人赛和全国赛持续突破",
            tags: ["IM", "亚洲个人赛", "全国锦标赛"]
        ),
        .init(
            fideID: "8620946",
            chineseName: "陈一宁",
            pinyinName: "chen yining",
            englishName: "Chen, Yining",
            birthYear: "2009",
            focus: "女子青年主力，青少年赛和成人赛双线积累",
            tags: ["FM/WIM", "李成智杯", "女子赛事"]
        ),
        .init(
            fideID: "8627215",
            chineseName: "姜天瑜",
            pinyinName: "jiang tianyu",
            englishName: "Jiang, Tianyu",
            birthYear: "2010",
            focus: "女子青年上升期，全国女锦标赛与世青赛活跃",
            tags: ["全国女锦标赛", "世青赛", "青年冠军"]
        )
    ]
}
