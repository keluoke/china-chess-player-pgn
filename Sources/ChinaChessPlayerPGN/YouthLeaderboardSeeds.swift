import Foundation

enum YouthLeaderboardSeeds {
    static let players: [ChinesePlayerSeed] = [
        seed(fideID: "8673853", englishName: "Jin, Yuxin", birthYear: 2018, standard: 1738, rapid: 1783, blitz: 1739),
        seed(fideID: "8690316", englishName: "Zhu, Xianshuo", birthYear: 2018, standard: 1676, rapid: nil, blitz: nil),
        seed(fideID: "574001280", englishName: "Xie, Jinyan", birthYear: 2019, standard: 1588, rapid: 1534, blitz: nil),
        seed(fideID: "8672733", englishName: "Hua, Yize", birthYear: 2018, standard: 1583, rapid: nil, blitz: nil),
        seed(fideID: "8678529", englishName: "Xue, Jingwei", birthYear: 2019, standard: 1577, rapid: 1681, blitz: 1515),

        seed(fideID: "8655090", englishName: "Guo, Ziming", birthYear: 2016, standard: 1968, rapid: 1865, blitz: 1840),
        seed(fideID: "8678928", englishName: "Tao, Yuxuan", birthYear: 2017, standard: 1885, rapid: nil, blitz: nil),
        seed(fideID: "8657092", englishName: "Yu, Zixiao", birthYear: 2016, standard: 1865, rapid: 1748, blitz: 1545),
        seed(fideID: "8652937", englishName: "Zheng, Gaozhi", birthYear: 2016, standard: 1864, rapid: nil, blitz: nil),
        seed(fideID: "8678804", englishName: "Dong, Yezhongxuan", birthYear: 2017, standard: 1856, rapid: nil, blitz: nil),

        seed(fideID: "8649138", englishName: "Yu, Zechen", birthYear: 2014, standard: 2273, rapid: 1868, blitz: 2034),
        seed(fideID: "8649464", englishName: "Yuan, Shunzhe", birthYear: 2015, standard: 2151, rapid: 1775, blitz: 1763),
        seed(fideID: "8641447", englishName: "Zuo, Junyu", birthYear: 2014, standard: 2062, rapid: 1966, blitz: 1945),
        seed(fideID: "8647526", englishName: "Zhou, Guoyu", birthYear: 2014, standard: 1991, rapid: 1858, blitz: 1853),
        seed(fideID: "8656266", englishName: "Lv, Ruiqi", birthYear: 2014, standard: 1988, rapid: 1882, blitz: 2023),

        seed(fideID: "8640653", englishName: "Jiang, Liu", birthYear: 2012, standard: 2323, rapid: 2029, blitz: 2030),
        seed(fideID: "8641528", englishName: "Yang, Zilong", birthYear: 2012, standard: 2317, rapid: 1972, blitz: 2227),
        seed(fideID: "8641552", englishName: "Zhang, Haoxuan", birthYear: 2013, standard: 2253, rapid: 1822, blitz: 2082),
        seed(fideID: "8642230", englishName: "You, Qingyi", birthYear: 2013, standard: 2201, rapid: nil, blitz: nil),
        seed(fideID: "8643172", englishName: "Zhang, Haoxuan(ZJ)", birthYear: 2012, standard: 2168, rapid: 2018, blitz: 2044),

        seed(fideID: "8631930", englishName: "Jiang, Haochen", birthYear: 2011, standard: 2456, rapid: 2436, blitz: 2473),
        seed(fideID: "8618020", englishName: "Lu, Miaoyi", birthYear: 2010, standard: 2419, rapid: 2287, blitz: 2244),
        seed(fideID: "8625417", englishName: "Wu, Kaige", birthYear: 2011, standard: 2403, rapid: 1609, blitz: 1519),
        seed(fideID: "8631603", englishName: "Xie, Jiaxiang", birthYear: 2011, standard: 2364, rapid: 2287, blitz: 2367),
        seed(fideID: "8626960", englishName: "Dong, Hongfu", birthYear: 2010, standard: 2353, rapid: 2271, blitz: 2290),

        seed(fideID: "8622388", englishName: "Xiao, Tong(QD)", birthYear: 2008, standard: 2586, rapid: 2438, blitz: 2445),
        seed(fideID: "8625492", englishName: "Xue, Haowen", birthYear: 2008, standard: 2553, rapid: 2442, blitz: 2407),
        seed(fideID: "8632200", englishName: "Kong, Xiangrui", birthYear: 2009, standard: 2513, rapid: 2398, blitz: 2469),
        seed(fideID: "8631891", englishName: "Meng, Yihan", birthYear: 2009, standard: 2478, rapid: 2447, blitz: 2442),
        seed(fideID: "63100630", englishName: "Xu, Ziyuan", birthYear: 2008, standard: 2455, rapid: 2225, blitz: 2088)
    ]

    private static func seed(
        fideID: String,
        englishName: String,
        birthYear: Int,
        standard: Int?,
        rapid: Int?,
        blitz: Int?
    ) -> ChinesePlayerSeed {
        ChinesePlayerSeed(
            fideID: fideID,
            chineseName: "",
            pinyinName: pinyinName(from: englishName),
            englishName: englishName,
            federation: "CHN",
            aliases: aliases(for: englishName, fideID: fideID),
            birthYear: birthYear,
            standardRating: standard,
            rapidRating: rapid,
            blitzRating: blitz
        )
    }

    private static func pinyinName(from englishName: String) -> String {
        englishName
            .replacingOccurrences(of: ",", with: "")
            .replacingOccurrences(of: "(", with: " ")
            .replacingOccurrences(of: ")", with: " ")
            .split(separator: " ")
            .map { $0.lowercased() }
            .joined(separator: " ")
    }

    private static func aliases(for englishName: String, fideID: String) -> [String] {
        var values = [englishName, englishName.replacingOccurrences(of: ",", with: ""), fideID]
        let pieces = englishName
            .replacingOccurrences(of: ",", with: " ")
            .replacingOccurrences(of: "(", with: " ")
            .replacingOccurrences(of: ")", with: " ")
            .split(separator: " ")
            .map(String.init)
        if pieces.count >= 2 {
            values.append(pieces.joined(separator: " "))
            values.append((pieces.dropFirst() + pieces.prefix(1)).joined(separator: " "))
        }

        var seen = Set<String>()
        return values.filter { value in
            let normalized = value.lowercased()
            guard !seen.contains(normalized) else { return false }
            seen.insert(normalized)
            return true
        }
    }
}
